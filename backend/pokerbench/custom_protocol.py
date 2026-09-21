"""Text transports for personal agents; official adapter prompts stay unchanged."""
from urllib.parse import urlsplit

from .custom_endpoint import validate_endpoint

SUFFIXES = {'chat_completions': '/chat/completions', 'responses': '/responses', 'anthropic': '/messages'}


def agent_endpoint(url, api_format):
    base = validate_endpoint(url)
    path = urlsplit(url).path.rstrip('/')
    for kind, suffix in SUFFIXES.items():
        if path.endswith(suffix):
            if kind != api_format:
                raise ValueError('Endpoint path does not match the selected API format.')
            if kind != 'chat_completions':
                base = base[:-len(suffix)].rstrip('/')
            break
    if urlsplit(base).hostname in ('api.openai.com', 'api.anthropic.com') and not urlsplit(base).path:
        base += '/v1'
    return base


def build_request(entry, payload, key):
    kind = entry.api_format
    headers = {'Authorization': f'Bearer {key}'}
    if kind == 'responses':
        fmt = payload['response_format']
        fmt = {'type': 'json_schema', **fmt['json_schema']} if fmt['type'] == 'json_schema' else fmt
        body = {'model': entry.model, 'input': payload['messages'], 'max_output_tokens': payload['max_tokens'],
                'text': {'format': fmt}, 'store': False}
    elif kind == 'anthropic':
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
        body = {'model': entry.model, 'max_tokens': payload['max_tokens'],
                'messages': [m for m in payload['messages'] if m['role'] not in ('system', 'developer')]}
        system = [m['content'] for m in payload['messages'] if m['role'] in ('system', 'developer')]
        if system:
            body['system'] = '\n\n'.join(system)
    else:
        body = dict(payload)
        if urlsplit(entry.base_url).hostname == 'api.openai.com':
            body['max_completion_tokens'] = body.pop('max_tokens')
            body['store'] = False
    return entry.base_url.rstrip('/') + SUFFIXES[kind], headers, body


def chat_response(raw, api_format):
    """Normalize successful text responses for the existing adapter decoder."""
    if api_format == 'chat_completions':
        return raw
    usage = raw.get('usage') or {}
    if api_format == 'anthropic':
        complete = raw.get('stop_reason') == 'end_turn'
        blocks = raw.get('content', [])
        text = ''.join(b['text'] for b in blocks if b.get('type') == 'text')
        # Tool use and refusals are not poker decisions.
        complete = complete and all(b.get('type') in ('text', 'thinking', 'redacted_thinking') for b in blocks)
    else:
        complete = raw.get('status') == 'completed' and all(
            item.get('type') in ('message', 'reasoning') for item in raw.get('output', []))
        blocks = [b for item in raw.get('output', []) if item.get('type') == 'message'
                  for b in item.get('content', [])]
        text = ''.join(b['text'] for b in blocks if b.get('type') == 'output_text')
        complete = complete and all(b.get('type') == 'output_text' for b in blocks)
    return {'model': raw.get('model'),
            'usage': {'prompt_tokens': usage.get('input_tokens'), 'completion_tokens': usage.get('output_tokens')},
            'choices': [{'message': {'content': text}, 'finish_reason': 'stop' if complete and text else 'incomplete'}]}
