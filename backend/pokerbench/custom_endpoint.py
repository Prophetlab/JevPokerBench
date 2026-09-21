"""Public HTTPS endpoints for user-supplied OpenAI-compatible credentials."""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx

__all__ = ['validate_endpoint', 'custom_client']

_INVALID = 'Custom endpoint must be a public HTTPS URL without credentials, query, or fragment.'
_NETWORK_ERROR = 'Custom endpoint connection failed or address is not public.'
_LOCAL_SUFFIXES = ('localhost', 'local', 'localdomain', 'internal', 'lan', 'home', 'home.arpa')


def _public_ip(value):
    address = ipaddress.ip_address(value)
    if ('%' in value or not address.is_global or address.is_multicast
            or address.is_reserved or address.is_loopback or address.is_link_local
            or address.is_unspecified or str(address) == '168.63.129.16'):
        raise ValueError(_INVALID)
    if isinstance(address, ipaddress.IPv6Address):
        # Exclude mapped/translated/tunnel addresses, including public IPv4 mappings.
        if (address.ipv4_mapped is not None or address.sixtofour is not None
                or address.teredo is not None
                or address not in ipaddress.ip_network('2000::/3')):
            raise ValueError(_INVALID)
    return address


def validate_endpoint(url: str) -> str:
    """Normalize a base URL (or full chat endpoint); never perform DNS here.

    Raise ValueError with a fixed, credential-free message on invalid input.
    Hostname addresses are checked later, on every new TCP connection.
    """
    try:
        if (not isinstance(url, str) or not url
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
                or any(c in url for c in ('\\', '?', '#'))
                or re.search(r'%(?![0-9a-fA-F]{2})', url)):
            raise ValueError(_INVALID)
        parts = urlsplit(url)
        parsed = httpx.URL(url)
        if (parsed.scheme != 'https' or not parsed.host or '@' in parts.netloc
                or parts.netloc.endswith(':')
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError(_INVALID)
        host = parsed.raw_host.decode('ascii').rstrip('.')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split('.')
            if (len(labels) < 2 or len(host) > 253
                    or not re.search(r'[a-z]', labels[-1])
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in labels)
                    or any(host == suffix or host.endswith('.' + suffix)
                           for suffix in _LOCAL_SUFFIXES)):
                raise ValueError(_INVALID)
        else:
            host = str(_public_ip(host))
        normalized = str(parsed.copy_with(host=host)).rstrip('/')
        if normalized.endswith('/chat/completions'):
            normalized = normalized[:-len('/chat/completions')].rstrip('/')
        return normalized
    except (ValueError, httpx.InvalidURL, UnicodeError):
        raise ValueError(_INVALID) from None


def _proxy_address():
    """Only the server may configure a trusted, literal loopback HTTP proxy."""
    from .config import Settings
    value = Settings().pokerbench_custom_proxy_url
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme != 'http' or not host or not ipaddress.ip_address(host).is_loopback
                or '@' in parts.netloc or parts.path not in ('', '/')
                or '?' in value or '#' in value or '\\' in value or '%' in value
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
                or parts.netloc.endswith(':')):
            raise ValueError
        port = parts.port if parts.port is not None else 80
        if not 1 <= port <= 65535:
            raise ValueError
        return str(ipaddress.ip_address(host)), port
    except (ValueError, UnicodeError):
        raise ValueError('Invalid server custom endpoint proxy configuration.') from None


class _PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, proxy=None):
        self._backend = httpcore.AnyIOBackend()
        self._proxy = proxy

    async def _tunnel(self, address, port, timeout):
        stream = await self._backend.connect_tcp(
            host=self._proxy[0], port=self._proxy[1], timeout=timeout,
        )
        try:
            authority = f'[{address}]:{port}' if ':' in address else f'{address}:{port}'
            await stream.write(
                f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode('ascii'),
                timeout=timeout,
            )
            headers = b''
            while b'\r\n\r\n' not in headers:
                chunk = await stream.read(4096, timeout=timeout)
                if not chunk:
                    raise httpcore.ConnectError(_NETWORK_ERROR)
                headers += chunk
                if len(headers) > 16384:
                    raise httpcore.ConnectError(_NETWORK_ERROR)
            status = headers.split(b'\r\n', 1)[0].split(b' ', 2)
            if (len(status) < 2 or status[0] not in (b'HTTP/1.0', b'HTTP/1.1')
                    or status[1] != b'200' or headers.find(b'\r\n\r\n') != len(headers) - 4):
                raise httpcore.ConnectError(_NETWORK_ERROR)
            # TLS is still started by httpcore with the original endpoint hostname.
            return stream
        except BaseException:
            with anyio.CancelScope(shield=True):
                await stream.aclose()
            raise

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            # One deadline covers DNS and all numeric connection attempts.
            with anyio.fail_after(timeout):
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    records = await anyio.getaddrinfo(
                        host, port, family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
                    )
                    addresses = []
                    for family, _, _, _, sockaddr in records:
                        address = _public_ip(sockaddr[0])
                        if (family not in (socket.AF_INET, socket.AF_INET6)
                                or (family == socket.AF_INET) != (address.version == 4)
                                or (family == socket.AF_INET6 and sockaddr[3] != 0)):
                            raise ValueError(_INVALID)
                        addresses.append(str(address))
                else:
                    addresses = [str(_public_ip(host))]
                # Validate the entire answer before allowing even the first connect.
                addresses = list(dict.fromkeys(addresses))
                if not addresses:
                    raise httpcore.ConnectError(_NETWORK_ERROR)
                # Probe TCP only: no completion requests or keys are sent here.
                for attempt in range(3):
                    address = addresses[attempt % len(addresses)]
                    try:
                        with anyio.fail_after(1):
                            return await self._backend.connect_tcp(
                                host=address, port=port, timeout=1,
                                local_address=local_address, socket_options=socket_options,
                            )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout, TimeoutError):
                        continue
                if self._proxy is not None:
                    return await self._tunnel(addresses[0], port, timeout)
                raise httpcore.ConnectError(_NETWORK_ERROR)
        except TimeoutError:
            raise httpcore.ConnectTimeout('Custom endpoint connection timed out.') from None
        except (OSError, ValueError):
            raise httpcore.ConnectError(_NETWORK_ERROR) from None


class _SafeResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                yield chunk
        except httpx.HTTPError as exc:
            raise type(exc)(_NETWORK_ERROR) from None

    async def aclose(self):
        try:
            await self._stream.aclose()
        except httpx.HTTPError as exc:
            raise type(exc)(_NETWORK_ERROR) from None


class _PublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self, endpoint, proxy):
        super().__init__(trust_env=False, retries=0)
        self._endpoint = httpx.URL(endpoint)
        # Private integration pinned to httpx==0.28.1 / httpcore==1.0.9.
        # httpcore keeps the URL host for TLS SNI/cert verification and HTTP Host;
        # only connect_tcp receives the validated numeric address. Re-test on upgrade.
        self._pool._network_backend = _PublicNetworkBackend(proxy)

    async def handle_async_request(self, request):
        url = request.url
        if (url.scheme != 'https' or url.raw_host != self._endpoint.raw_host
                or url.port != self._endpoint.port or url.userinfo or url.query or url.fragment):
            raise httpx.ConnectError(_INVALID)
        request.headers['Host'] = self._endpoint.netloc.decode('ascii')
        request.extensions['sni_hostname'] = self._endpoint.raw_host.decode('ascii')
        try:
            response = await super().handle_async_request(request)
        except httpx.HTTPError as exc:
            raise type(exc)(_NETWORK_ERROR) from None
        response.stream = _SafeResponseStream(response.stream)
        return response


def custom_client(endpoint: str, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    """Return an origin-bound client; use with ``async with`` and an absolute URL.

    Pass the result of validate_endpoint() as the provider's base, then POST to
    base + '/chat/completions'. Credentials belong in request headers only.
    POKERBENCH_CUSTOM_PROXY_URL may configure a loopback HTTP CONNECT fallback
    after three failed one-second direct TCP attempts. The caller's overall
    deadline must cover this client and the completion request.
    """
    base = validate_endpoint(endpoint)
    return httpx.AsyncClient(
        base_url=base, timeout=timeout, transport=_PublicTransport(base, _proxy_address()),
        follow_redirects=False, trust_env=False,
    )
