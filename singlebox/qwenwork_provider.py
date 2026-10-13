#!/usr/bin/env python3
"""Local deployment provisioning, never desktop/bridge configuration."""
import argparse
import json
import os
import re
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class Client:
    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.username or parsed.password or parsed.path not in {'', '/'}:
            raise ValueError('Provisioning requires a loopback HTTP service')
        self.url = url.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.url + path, data=body,
                                        headers={'Content-Type': 'application/json', **(headers or {})})
        with self.opener.open(request, timeout=15) as response:
            return json.load(response)


def save_private(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_provider(provider):
    if (provider.get('slug') != 'local' or provider.get('enabled') is not True
            or provider.get('auth_mode') != 'static_bearer' or provider.get('protocol_version') != '2.0'
            or not re.fullmatch(r'prv_[A-Za-z0-9_-]+', provider.get('provider_id', ''))):
        raise ValueError('Existing local Provider is incompatible; no Provider was modified')
    return provider['provider_id']


def ensure_provider(client, state: Path):
    try:
        provider = client.request('/providers/by-slug/local')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        try:
            client.request('/providers', {
                'name': 'Singlebox local external Bots', 'slug': 'local',
                'auth': {'mode': 'static_bearer'}, 'protocol_version': '2.0',
            }, {'X-Mock-User-Id': 'qwenwork-bootstrap'})
        except urllib.error.HTTPError as conflict:
            if conflict.code != 409:
                raise
        provider = client.request('/providers/by-slug/local')
    provider_id = validate_provider(provider)
    save_private(state, json.dumps({'provider_id': provider_id, 'slug': 'local'}).encode())
    return provider_id


def authorized_config(text: str, provider_id: str):
    document = tomllib.loads(text)
    section = document.get('openapi_v1', {})
    ids = section.get('registration_self_service_provider_ids', [])
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        raise ValueError('Invalid registration self-service configuration')
    ids = list(dict.fromkeys([*ids, provider_id]))
    line = 'registration_self_service_provider_ids = ' + json.dumps(ids) + '\n'
    match = re.search(r'^\[openapi_v1\][ \t]*(?:#.*)?$', text, re.MULTILINE)
    if match:
        end = re.search(r'^\[', text[match.end():], re.MULTILINE)
        stop = len(text) if end is None else match.end() + end.start()
        block = text[match.end():stop]
        block, count = re.subn(r'^registration_self_service_provider_ids[ \t]*=[ \t]*\[[\s\S]*?\][ \t]*(?:#.*)?(?:\n|$)', line, block, flags=re.MULTILINE)
        if count == 0:
            block = '\n' + line + block.lstrip('\n')
        result = text[:match.end()] + block + text[stop:]
    else:
        result = text.rstrip() + '\n\n[openapi_v1]\n' + line
    expected = dict(document)
    expected['openapi_v1'] = {**section, 'registration_self_service_provider_ids': ids}
    if tomllib.loads(result) != expected:
        raise ValueError('Cannot safely update authorization; configuration unchanged')
    return result


def provider_id_from_state(state: Path):
    value = json.loads(state.read_text())
    provider_id = value['provider_id']
    if value.get('slug') != 'local' or not re.fullmatch(r'prv_[A-Za-z0-9_-]+', provider_id):
        raise ValueError('Invalid local Provider state')
    return provider_id


def verify(client, provider_id: str):
    # These callers are distinct from the provisioning owner. No admin token.
    for user in ['qwenwork-validation-a', 'qwenwork-validation-b']:
        headers = {'Cookie': f'staff_id={user}; nick_name=QwenWorkValidation'}
        provider = client.request('/api/v1/collaboration/providers/by-slug/local', headers=headers)
        if validate_provider(provider) != provider_id:
            raise ValueError('Gateway Provider discovery does not match provisioning')
        result = client.request('/openapi/v1/collaboration/register/token?' + urllib.parse.urlencode({'provider_id': provider_id}), headers=headers)
        data = result.get('data', {})
        scope = data.get('registration', {})
        if (result.get('code') != 20000 or not data.get('token')
                or scope.get('token_version') != 2 or scope.get('provider_id') != provider_id
                or 'plugin' not in scope.get('allowed_modes', [])):
            raise ValueError('Ordinary-user scoped registration verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['ensure', 'config', 'configured', 'verify'])
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--url')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if args.command == 'ensure':
        if not args.url:
            parser.error('--url is required')
        ensure_provider(Client(args.url), args.state)
    elif args.command == 'config':
        if not args.config:
            parser.error('--config is required')
        if args.state.exists():
            text = args.config.read_text()
            updated = authorized_config(text, provider_id_from_state(args.state))
            if updated != text:
                save_private(args.config, updated.encode())
    elif args.command == 'configured':
        if not args.config:
            parser.error('--config is required')
        config = tomllib.loads(args.config.read_text())
        if provider_id_from_state(args.state) not in config.get('openapi_v1', {}).get('registration_self_service_provider_ids', []):
            raise SystemExit(1)
    else:
        if not args.url:
            parser.error('--url is required')
        verify(Client(args.url), provider_id_from_state(args.state))
        print('Provider discovery and v2 plugin registration passed for two ordinary users.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, urllib.error.URLError) as error:
        # Do not print response bodies, credentials or malformed config contents.
        raise SystemExit(f'QwenWork provisioning failed ({type(error).__name__}); check local service/configuration.') from None
