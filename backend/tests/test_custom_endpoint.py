import socket
import ssl

import anyio
import httpcore
import httpx
import pytest

from pokerbench.custom_endpoint import custom_client, validate_endpoint


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    monkeypatch.delenv('POKERBENCH_CUSTOM_PROXY_URL', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Tests must mock DNS; real lookups are forbidden')
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.mark.parametrize('url, expected', [
    ('https://API.example.com/', 'https://api.example.com'),
    ('https://api.example.com/v1/', 'https://api.example.com/v1'),
    ('https://api.example.com/v1/chat/completions/', 'https://api.example.com/v1'),
    ('https://api.example.com/chat/completions', 'https://api.example.com'),
    ('https://api.example.com:8443/proxy/openai/v1/chat/completions',
     'https://api.example.com:8443/proxy/openai/v1'),
    ('https://api.example.com/models/my%20model/v1', 'https://api.example.com/models/my%20model/v1'),
    ('https://api.example.com./v1', 'https://api.example.com/v1'),
    ('https://8.8.8.8/v1', 'https://8.8.8.8/v1'),
    ('https://[2606:4700:4700::1111]/v1', 'https://[2606:4700:4700::1111]/v1'),
    ('https://bücher.de/v1', 'https://xn--bcher-kva.de/v1'),
])
def test_normalize_without_dns(url, expected):
    assert validate_endpoint(url) == expected


BAD_IPS = [
    '127.0.0.1', '0.0.0.0', '10.1.2.3', '172.16.0.1', '192.168.1.1',
    '169.254.169.254', '169.254.170.2', '100.100.100.200', '168.63.129.16',
    '192.0.2.1', '198.18.0.1', '198.51.100.1', '203.0.113.1', '224.0.0.1',
    '240.0.0.1', '255.255.255.255', '::', '::1', 'fe80::1', 'fc00::1',
    'ff02::1', '2001:db8::1', '::ffff:127.0.0.1', '::ffff:8.8.8.8',
    '64:ff9b::7f00:1', '2002:7f00:1::', '2001::1',
]


@pytest.mark.parametrize('address', BAD_IPS)
def test_reject_nonpublic_ip_literals(address):
    host = '[' + address + ']' if ':' in address else address
    with pytest.raises(ValueError, match='public HTTPS'):
        validate_endpoint('https://' + host + '/v1')


@pytest.mark.parametrize('url', [
    '', None, 'http://api.example.com', 'ftp://api.example.com', '//api.example.com',
    'https://', 'https://user:secret@api.example.com', 'https://@api.example.com',
    'https://api.example.com?key=secret', 'https://api.example.com?',
    'https://api.example.com/#secret', 'https://api.example.com/#',
    'https://localhost', 'https://localhost.', 'https://a.localhost',
    'https://metadata.google.internal', 'https://a.local', 'https://a.localdomain',
    'https://a.lan', 'https://a.home.arpa', 'https://intranet',
    'https://127.1', 'https://2130706433', 'https://0x7f000001', 'https://0177.0.0.1',
    'https://0x7f.0.0.1', 'https://[fe80::1%25en0]',
    'https://api.example.com:0', 'https://api.example.com:65536', 'https://api.example.com:',
    'https://api.example.com\\@127.0.0.1', ' https://api.example.com',
    'https://api.example.com/\nsecret', 'https://api.example.com/%zz',
    'https://api..example.com', 'https://-api.example.com', 'https://api_example.com',
    'https://%31%32%37.0.0.1',
])
def test_reject_invalid_endpoints_without_echoing_input(url):
    with pytest.raises(ValueError) as exc:
        validate_endpoint(url)
    assert str(exc.value) == 'Custom endpoint must be a public HTTPS URL without credentials, query, or fragment.'


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, responses):
        self.responses = list(responses)
        self.writes = []
        self.tls = []
        self.closed = False

    async def read(self, max_bytes, timeout=None):
        return self.responses.pop(0) if self.responses else b''

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((ssl_context, server_hostname))
        return self

    async def aclose(self):
        self.closed = True

    def get_extra_info(self, info):
        return False if info == 'is_readable' else None


OK = b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'


@pytest.fixture
def network(monkeypatch):
    class Network:
        answers = ['8.8.8.8', '2606:4700:4700::1111']
        responses = [OK, OK]
        dns = []
        connections = []
        streams = []
        failures = set()
        options = []

    state = Network()

    async def resolve(host, port, **kwargs):
        state.dns.append((host, port))
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET,
                 socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
                 (ip, port, 0, 0) if ':' in ip else (ip, port)) for ip in state.answers]

    async def connect(backend, host, port, **kwargs):
        state.connections.append((host, port))
        state.options.append(kwargs)
        if host in state.failures:
            raise httpcore.ConnectError('secret should never escape')
        stream = Stream(state.responses)
        state.streams.append(stream)
        return stream

    monkeypatch.setattr(anyio, 'getaddrinfo', resolve)
    monkeypatch.setattr(httpcore.AnyIOBackend, 'connect_tcp', connect)
    return state


@pytest.mark.asyncio
async def test_pinned_numeric_connection_sni_host_and_pool_reuse(network):
    base = validate_endpoint('https://api.example.com:8443/proxy/v1/chat/completions/')
    async with custom_client(base, 2) as client:
        for _ in range(2):
            response = await client.post(base + '/chat/completions',
                                         headers={'Authorization': 'Bearer test-secret'}, json={'model': 'mine'})
            assert response.json() == {}
            # A changed DNS answer cannot affect the already pinned connection.
            network.answers = ['127.0.0.1']
    assert network.dns == [('api.example.com', 8443)]
    assert network.connections == [('8.8.8.8', 8443)]
    stream = network.streams[0]
    context, hostname = stream.tls[0]
    assert hostname == 'api.example.com'
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    sent = b''.join(stream.writes)
    assert sent.count(b'Host: api.example.com:8443\r\n') == 2
    assert b'POST /proxy/v1/chat/completions HTTP/1.1' in sent
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('address', BAD_IPS)
@pytest.mark.parametrize('mixed', [False, True])
async def test_dns_rejects_every_nonpublic_address_before_connect(network, address, mixed):
    network.answers = ['8.8.8.8', address] if mixed else [address]
    async with custom_client('https://api.example.com/v1', 2) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post('https://api.example.com/v1/chat/completions')
    assert network.connections == []


@pytest.mark.asyncio
async def test_rebinding_new_connection_is_revalidated(network):
    network.responses = [b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}']
    async with custom_client('https://api.example.com', 2) as client:
        assert (await client.post('/chat/completions')).status_code == 200
        network.answers = ['127.0.0.1']
        with pytest.raises(httpx.ConnectError):
            await client.post('/chat/completions')
    assert len(network.dns) == 2
    assert network.connections == [('8.8.8.8', 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
async def test_redirects_are_not_followed(network, status):
    network.responses = [f'HTTP/1.1 {status} Redirect\r\nLocation: http://169.254.169.254/secret\r\nContent-Length: 0\r\n\r\n'.encode()]
    async with custom_client('https://api.example.com', 2) as client:
        response = await client.post('/chat/completions', headers={'Authorization': 'Bearer test-secret'})
        assert response.status_code == status and not response.history
        assert client.follow_redirects is False
    assert network.connections == [('8.8.8.8', 443)]


@pytest.mark.asyncio
async def test_proxy_and_certificate_environment_ignored(network, monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.setenv(name, 'http://proxy:secret@127.0.0.1:9999')
    monkeypatch.setenv('SSL_CERT_FILE', '/does/not/exist')
    monkeypatch.setenv('SSL_CERT_DIR', '/does/not/exist')
    async with custom_client('https://api.example.com', 2) as client:
        assert client.trust_env is False
        assert (await client.post('/chat/completions')).status_code == 200
    assert network.dns == [('api.example.com', 443)]
    assert network.connections == [('8.8.8.8', 443)]


@pytest.mark.asyncio
async def test_public_ipv6_fallback_stays_numeric(network):
    network.failures = {'8.8.8.8'}
    async with custom_client('https://api.example.com', 2) as client:
        assert (await client.post('/chat/completions')).status_code == 200
    assert network.connections == [('8.8.8.8', 443), ('2606:4700:4700::1111', 443)]
    assert network.streams[0].tls[0][1] == 'api.example.com'


@pytest.mark.asyncio
async def test_empty_dns_fails_closed(network):
    network.answers = []
    async with custom_client('https://api.example.com', 2) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post('/chat/completions')
    assert not network.connections


@pytest.mark.asyncio
async def test_dns_timeout_is_part_of_connect_timeout(network, monkeypatch):
    async def stalled(*args, **kwargs):
        await anyio.sleep_forever()
    monkeypatch.setattr(anyio, 'getaddrinfo', stalled)
    async with custom_client('https://api.example.com', .01) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.post('/chat/completions')
    assert not network.connections


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['dns', 'connect', 'tls', 'read'])
async def test_network_errors_do_not_expose_secrets(network, monkeypatch, kind):
    async def fail(*args, **kwargs):
        if kind == 'dns':
            raise OSError('test-secret')
        raise httpcore.ReadError('test-secret') if kind == 'read' else httpcore.ConnectError('test-secret')
    target, method = {'dns': (anyio, 'getaddrinfo'), 'connect': (httpcore.AnyIOBackend, 'connect_tcp'),
                      'tls': (Stream, 'start_tls'), 'read': (Stream, 'read')}[kind]
    monkeypatch.setattr(target, method, fail)
    async with custom_client('https://api.example.com', 2) as client:
        with pytest.raises(httpx.HTTPError) as exc:
            await client.post('/chat/completions', headers={'Authorization': 'Bearer test-secret'})
    assert 'test-secret' not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['http://api.example.com', 'https://127.0.0.1',
                                     'https://other.example.com', 'https://api.example.com:444',
                                     'https://secret@api.example.com', 'https://api.example.com?secret'])
async def test_client_is_bound_to_validated_https_origin(network, url):
    async with custom_client('https://api.example.com', 2) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(url)
    assert not network.dns and not network.connections


@pytest.mark.asyncio
async def test_host_and_sni_cannot_be_overridden(network):
    async with custom_client('https://api.example.com', 2) as client:
        await client.post('/chat/completions', headers={'Host': 'localhost'},
                          extensions={'sni_hostname': 'localhost'})
    assert network.streams[0].tls[0][1] == 'api.example.com'
    assert b'Host: api.example.com\r\n' in b''.join(network.streams[0].writes)


@pytest.mark.asyncio
@pytest.mark.parametrize('host, ip', [('8.8.8.8', '8.8.8.8'),
                                     ('[2606:4700:4700::1111]', '2606:4700:4700::1111')])
async def test_public_literal_connects_without_dns(network, host, ip):
    async with custom_client('https://' + host, 2) as client:
        assert (await client.post('/chat/completions')).status_code == 200
    assert not network.dns
    assert network.connections == [(ip, 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize('address, authority', [('8.8.8.8', '8.8.8.8:443'),
                                              ('2606:4700:4700::1111', '[2606:4700:4700::1111]:443')])
async def test_proxy_after_three_direct_failures_pins_target_and_keeps_tls(network, monkeypatch, address, authority):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    network.answers = [address]
    network.failures = {address}
    network.responses = [b'HTTP/1.1 200 Connection established\r\n', b'\r\n', OK]
    async with custom_client('https://api.example.com/v1', 15) as client:
        assert (await client.post('https://api.example.com/v1/chat/completions',
                                  headers={'Authorization': 'Bearer test-secret'}, json={'model': 'mine'})).status_code == 200
    assert network.connections == [(address, 443)] * 3 + [('127.0.0.1', 7890)]
    assert all(options['timeout'] == 1 for options in network.options[:3])
    assert network.dns == [('api.example.com', 443)]
    stream = network.streams[0]
    assert stream.writes[0] == f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode()
    assert b'test-secret' not in stream.writes[0]
    assert b''.join(stream.writes).count(b'POST /v1/chat/completions HTTP/1.1') == 1
    context, hostname = stream.tls[0]
    assert hostname == 'api.example.com' and context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED and stream.closed


@pytest.mark.asyncio
async def test_proxy_unused_when_direct_connect_succeeds(network, monkeypatch):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    async with custom_client('https://api.example.com', 15) as client:
        await client.post('/chat/completions')
    assert network.connections == [('8.8.8.8', 443)]


@pytest.mark.asyncio
async def test_private_dns_never_reaches_proxy(network, monkeypatch):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    network.answers = ['8.8.8.8', '169.254.169.254']
    async with custom_client('https://api.example.com', 15) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post('/chat/completions')
    assert not network.connections


@pytest.mark.asyncio
async def test_certificate_failure_does_not_trigger_proxy(network, monkeypatch):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    async def invalid_certificate(*args, **kwargs):
        raise httpcore.ConnectError('CERTIFICATE_VERIFY_FAILED test-secret')
    monkeypatch.setattr(Stream, 'start_tls', invalid_certificate)
    async with custom_client('https://api.example.com', 15) as client:
        with pytest.raises(httpx.ConnectError) as exc:
            await client.post('/chat/completions')
    assert 'test-secret' not in str(exc.value)
    assert network.connections == [('8.8.8.8', 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    b'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n',
    b'HTTP/1.1 302 Redirect\r\nLocation: http://localhost/\r\n\r\n',
    b'HTTP/1.1 500 test-secret\r\n\r\n', b'not-http 200\r\n\r\n',
    b'HTTP/1.1 2000 Bad\r\n\r\n', b'HTTP/1.1 200 OK\r\n\r\nextra',
    b'HTTP/1.1 200 OK\r\nX: ' + b'x' * 16384, b'',
])
async def test_bad_proxy_response_is_closed_and_sanitized(network, monkeypatch, response):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    network.failures = set(network.answers)
    network.responses = [response]
    async with custom_client('https://api.example.com', 15) as client:
        with pytest.raises(httpx.ConnectError) as exc:
            await client.post('/chat/completions')
    assert 'test-secret' not in str(exc.value)
    assert len(network.connections) == 4
    assert network.streams[0].closed and not network.streams[0].tls
    assert len(network.streams[0].writes) == 1


@pytest.mark.parametrize('proxy', [
    'http://private.example.com:7890', 'http://8.8.8.8:7890', 'https://127.0.0.1:7890',
    'socks5://127.0.0.1:7891', 'http://secret@127.0.0.1:7890',
    'http://127.0.0.1:7890?secret', 'http://127.0.0.1:7890/#secret',
    'http://127.0.0.1:7890/path', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
])
def test_invalid_server_proxy_configuration_is_sanitized(proxy, monkeypatch):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', proxy)
    with pytest.raises(ValueError, match='Invalid server custom endpoint proxy configuration') as exc:
        custom_client('https://api.example.com', 15)
    assert 'secret' not in str(exc.value)


@pytest.mark.asyncio
async def test_proxy_setting_and_connections_are_not_cached_across_clients(network, monkeypatch):
    network.answers = ['8.8.8.8']
    network.failures = {'8.8.8.8'}
    network.responses = [b'HTTP/1.1 200 OK\r\n\r\n', OK]
    for port in (7890, 7892):
        monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', f'http://127.0.0.1:{port}')
        async with custom_client('https://api.example.com', 15) as client:
            await client.post('/chat/completions')
    assert network.connections == ([('8.8.8.8', 443)] * 3 + [('127.0.0.1', 7890)]
                                   + [('8.8.8.8', 443)] * 3 + [('127.0.0.1', 7892)])
    assert len(network.dns) == 2 and all(stream.closed for stream in network.streams)


@pytest.mark.asyncio
async def test_caller_deadline_cancels_proxy_handshake_and_closes_stream(network, monkeypatch):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    network.failures = set(network.answers)
    async def stalled_read(*args, **kwargs):
        await anyio.sleep_forever()
    monkeypatch.setattr(Stream, 'read', stalled_read)
    async with custom_client('https://api.example.com', 15) as client:
        with pytest.raises(TimeoutError):
            with anyio.fail_after(.01):
                await client.post('/chat/completions')
    assert len(network.connections) == 4 and network.streams[0].closed


@pytest.mark.asyncio
async def test_direct_timeouts_use_three_tcp_probes_then_proxy(network, monkeypatch):
    monkeypatch.setenv('POKERBENCH_CUSTOM_PROXY_URL', 'http://127.0.0.1:7890')
    network.responses = [b'HTTP/1.1 200 OK\r\n\r\n', OK]
    connect = httpcore.AnyIOBackend.connect_tcp
    attempts = []
    async def timeout_direct(backend, host, port, **kwargs):
        if host != '127.0.0.1':
            attempts.append((host, kwargs['timeout']))
            raise httpcore.ConnectTimeout('test-secret')
        return await connect(backend, host, port, **kwargs)
    monkeypatch.setattr(httpcore.AnyIOBackend, 'connect_tcp', timeout_direct)
    async with custom_client('https://api.example.com', 15) as client:
        assert (await client.post('/chat/completions')).status_code == 200
    assert len(attempts) == 3 and all(timeout == 1 for _, timeout in attempts)
    assert network.connections == [('127.0.0.1', 7890)]
