"""Operator misconfiguration journeys: invoke the real executable's startup.

No calls to validators or factories from tests, no test-only config endpoint.
Each case must fail before serving HTTP and report the expected field/category.
"""
import copy
import json
import subprocess


def reject(env, config, root, expected, overrides=None, raw=None, suffix='json'):
    path = root / ('invalid.' + suffix)
    path.write_text(raw if raw is not None else json.dumps(config))
    child_env = env.env()
    for key, value in (overrides or {}).items():
        if value is None:
            child_env.pop(key, None)
        else:
            child_env[key] = value
    try:
        process = subprocess.run([env.binary, '-c', str(path)], cwd=root, env=child_env,
                                 capture_output=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise AssertionError('invalid configuration unexpectedly started BCS') from None
    output = (process.stderr + process.stdout).decode(errors='replace')
    assert process.returncode != 0 and expected in output, (
        f'configuration rejection must name {expected!r}, got exit={process.returncode}: {output[-600:]}')
    # Credentials must not appear in validation diagnostics.
    assert env.key not in output and env.stub.secret not in output
    env.checks.append({'startup': 'rejected', 'expected': expected})


def invalid_configs(env):
    config, data, base = env.config()
    del config['api']['auth']['success_redirect_path']
    with env.server(config, data, base):
        browser = env.browser()
        urls = browser.urls(base)
        callback = browser.authorize(next(item['url'] for item in urls if item['name'] == 'github'))
        _, headers, _ = browser.request('GET', callback, 302)
        assert headers['Location'] == '/', 'omitted success redirect must default to root'

    baseline, root, _ = env.config()
    mismatched = copy.deepcopy(baseline)
    mismatched['auth']['oauth']['jwt_secret'] = env.key + '-different'
    reject(env, mismatched, root, 'session_signing_key_mismatch')
    cases = []

    def field(name, value, expected=None):
        cases.append((name, value, expected or 'api.auth.' + name))

    for value in ([], ['unknown'], ['gateway', 'gateway'], ['Bad-name']):
        field('chain', value, 'api.auth.chain')
    for value in ('relative', 'ftp://example.com', 'http://example.com',
                  'https://user:pass@example.com', 'https://example.com?query=1',
                  'https://example.com#fragment', ''):
        field('public_base_url', value)
    for value in ('https://elsewhere.example', '//elsewhere.example', '/\\bad', '/bad\n', ''):
        field('success_redirect_path', value)
    for value in (0, 2**63 - 1):
        field('session_idle_timeout_minutes', value)
    field('session_signing_key_secret', '')
    for value in ([], ['*'], ['null'], ['https://bad/path'], ['https://bad?query'],
                  ['https://bad#fragment'], ['https://a@b'], ['ftp://bad'],
                  ['https://'], [' https://bad'], ['https://bad host'],
                  ['https://bad:port'], ['https://a:b:c'], ['https://[::1'],
                  ['https://[::1]extra'], ['https://good', 'https://good']):
        field('trusted_browser_origins', value, 'origin')
    field('unregistered', {'client_id': 'e2e'}, 'unknown_source')
    for value in ('not-a-table', {'signing_key_secret': ''}, {'typo': 'x'},
                  {'signing_key_secret': 'gateway', 'issuers': 'gateway'},
                  {'signing_key_secret': 'gateway', 'issuers': []},
                  {'signing_key_secret': 'gateway', 'issuers': [1]},
                  {'signing_key_secret': 'gateway', 'issuers': ['']},
                  {'signing_key_secret': 'gateway', 'issuers': ['same', 'same']},
                  {'signing_key_secret': 'gateway', 'audience': ''},
                  {'signing_key_secret': 'gateway', 'key_id': True}):
        field('gateway', value)
    for provider in ('github', 'google', 'wechat', 'alipay'):
        field(provider, [], 'api.auth.' + provider)
        field(provider, {'client_id': 'client', 'typo': 'bad'}, 'unknown_field')
        field(provider, {'client_id': ''})
    for name, value, expected in cases:
        config = copy.deepcopy(baseline)
        config['api']['auth'][name] = value
        reject(env, config, root, expected)

    # Required public fields and per-source table presence are startup-fatal.
    for name in ('chain', 'public_base_url', 'session_signing_key_secret',
                 'github', 'gateway'):
        config = copy.deepcopy(baseline)
        del config['api']['auth'][name]
        reject(env, config, root, name)
    for provider in ('github', 'alipay'):
        for field_name in baseline['api']['auth'][provider]:
            config = copy.deepcopy(baseline)
            config['api']['auth'][provider][field_name] = 1
            reject(env, config, root, 'api.auth.' + provider)

    # OAuth-only options cannot silently linger on a gateway-only deployment.
    config = copy.deepcopy(baseline)
    config['api']['auth']['chain'] = ['gateway']
    reject(env, config, root, 'oauth_fields_without_oauth_source')
    gateway, _, _ = env.config('gateway')
    gateway['api']['auth']['trusted_browser_origins'] = ['https://example.com']
    reject(env, gateway, root, 'no cookie source')

    # Secret references are resolved by the production secret backend.
    for key, value, expected in (
        ('OAUTH_E2E_SESSION', None, 'secret_unavailable'),
        ('OAUTH_E2E_CLIENT', '', 'blank_value'),
        ('OAUTH_E2E_PRIVATE', 'not-a-key', 'invalid_provider_keys'),
        ('OAUTH_E2E_PUBLIC', 'not-a-key', 'invalid_provider_keys'),
        ('OAUTH_E2E_GATEWAY', None, 'secret_unavailable'),
    ):
        reject(env, baseline, root, expected, {key: value})

    # Both file formats traverse the same native-value guard before merge.
    for value in (None, 1.5, 2**63):
        config = copy.deepcopy(baseline)
        config['api']['auth']['github']['client_id'] = value
        reject(env, config, root, 'api.auth.github.client_id')
    for value in ('2026-11-03', '1.5', 'nan'):
        reject(env, baseline, root, 'api.auth', raw='[api.auth]\nchain=["gateway"]\nvalue=' + value + '\n', suffix='toml')
