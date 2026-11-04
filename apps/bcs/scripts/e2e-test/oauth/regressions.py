"""Cross-entry deployment regressions, exercised only through live HTTP."""
from harness import claims
from flows import user, V1, LEGACY


def default_environment(env):
    # Only the child loses environment selection; the harness process is unchanged.
    process_env = env.env()
    for name in ('SERVER_ENV', 'REAL_SERVER_ENV', 'ALIPAY_APP_ENV'):
        process_env.pop(name, None)
    for memory in (False, True):
        config, root, base = env.config()
        with env.server(config, root, base, memory, process_env=process_env):
            browser = env.browser()
            identity = None
            for family, other in ((LEGACY, V1), (V1, LEGACY)):
                token = browser.login(base, user='default-env-user', family=family)
                assert claims(token)['env'] == 'local', 'keep the existing OAuth identity namespace'
                current = user(browser, base, family=family)['user_id']
                assert user(browser, base, family=other)['user_id'] == current
                if identity is not None:
                    assert current == identity, 'changing login entry must not allocate a new user'
                identity = current
                browser.request('POST', base + other + '/refresh', headers={'Origin': base})
                rotated = browser.cookie()
                assert claims(rotated)['session_id'] == claims(token)['session_id']
                assert claims(rotated)['revision'] == claims(token)['revision'] + 1
                for entry in (V1, LEGACY):
                    user(env.browser(), base, token, 401, family=entry)
                    assert user(browser, base, family=entry)['user_id'] == identity
                browser.request('POST', base + family + '/logout', headers={'Origin': base})
                for entry in (V1, LEGACY):
                    user(env.browser(), base, rotated, 401, family=entry)


def rollback_modes(env):
    # Start with both configurations, then actually remove api.auth in the same DB.
    config, root, base = env.config()
    with env.server(config, root, base):
        browser = env.browser()
        browser.login(base)
        identity = user(browser, base)['user_id']
    config.pop('api')
    with env.server(config, root, base):
        assert user(browser, base)['user_id'] == identity
        browser.request('GET', base + '/openapi/v1/collaboration/groups', 401)
        assert user(browser, base, family=LEGACY)['user_id'] == identity
        # Removing api.auth restores the legacy-configured V1 OAuth facade.
        browser.urls(base)
        browser.request('POST', base + V1 + '/refresh', headers={'Origin': base})
        browser.request('POST', base + V1 + '/logout', headers={'Origin': base})
        browser.login(base)
        assert user(browser, base, family=LEGACY)['user_id'] == identity
    config['api'] = {'auth': {'chain': ['gateway'],
                             'gateway': {'signing_key_secret': 'gateway'}}}
    with env.server(config, root, base):
        browser = env.browser()
        for method, path in [('GET', '/url'), ('GET', '/callback/github?code=x&state=x'),
                             ('POST', '/refresh'), ('POST', '/logout')]:
            body, _, _ = browser.request(method, base + V1 + path, 404)
            assert body['data']['error_code'] == 'auth_not_configured'
        browser.login(base, family=LEGACY)  # Gateway-only closes V1, not legacy.
    config['auth'] = {'chain': ['session']}
    with env.server(config, root, base):
        browser = env.browser()
        for family in (V1, LEGACY):
            for method, path in [('GET', '/url'), ('GET', '/callback/github?code=x&state=x'),
                                 ('POST', '/refresh'), ('POST', '/logout')]:
                browser.request(method, base + family + path, 404)
        assert user(browser, base, extra={'x-avernet-principal': env.gateway()})['provider'] == 'gateway'


def unlisted_provider_does_not_block_gateway(env):
    config, root, base = env.config(chain=['google', 'gateway'])
    with env.server(config, root, base):
        browser = env.browser()
        browser.login(base, provider='github', family=LEGACY)
        user(browser, base, expected=401)
        assert user(browser, base, extra={'x-avernet-principal': env.gateway()})['provider'] == 'gateway'
        # This is not permission to rotate a disabled provider through V1.
        body, _, _ = browser.request('POST', base + V1 + '/refresh', 403, {'Origin': base})
        assert body['data']['error_code'] == 'session_forbidden'
        user(env.browser(), base, 'invalid', 401, extra={'x-avernet-principal': env.gateway()})
