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


def invite_code_issuance_requires_authorization(env):
    # Real provider login, strict session, router, application and SQLite store.
    # The first deployment denies all manual issuance, including Gateway users.
    config, root, base = env.config()
    config['invite'].update(invite_code_gate_enabled=True, public_claim_enabled=True,
                            public_claim_max_count=0, issuer_user_ids=[])
    init = base + '/api/v1/collaboration/invite-codes/init'
    public = base + '/openapi/v1/collaboration/invite-codes/'
    headers = {'Origin': base, 'Content-Type': 'application/json'}
    with env.server(config, root, base):
        ordinary, issuer = env.browser(), env.browser()
        ordinary.login(base, user='ordinary-invite-user')
        issuer.login(base, user='authorized-invite-issuer')
        issuer_id = user(issuer, base)['user_id']
        for browser in (ordinary, issuer):
            body, _, _ = browser.request('GET', public + 'me')
            assert body['data']['bound'] is False
            browser.request('POST', public + 'claim', 429)
            body, _, _ = browser.request('POST', init, 403, headers, {'count': 1})
            assert body['data']['error_code'] == 'forbidden'
        env.browser().request('POST', init, 403,
            {**headers, 'x-avernet-principal': env.gateway()}, {'count': 1})
        env.browser().request('POST', init, 401, headers, {'count': 1})

    # Restart the same database with one explicitly authorized verified user ID.
    config['invite']['issuer_user_ids'] = [issuer_id]
    with env.server(config, root, base):
        # A later Gateway identity must not replace the first successful OAuth
        # identity when application authorization rejects that OAuth user.
        ordinary.request('POST', init, 403,
            {**headers, 'x-avernet-principal': env.gateway(issuer_id)}, {'count': 1})
        body, _, _ = ordinary.request('GET', public + 'me')
        assert body['data']['bound'] is False
        body, _, _ = issuer.request('POST', init, 200, headers, {'count': 1})
        code = body['data']['codes'][0]
        ordinary.request('POST', public + 'bind', 200, headers, {'code': code})
        body, _, _ = ordinary.request('GET', public + 'me')
        assert body['data']['bound'] is True
        ordinary.request('POST', init, 403, headers, {'count': 1})
        ordinary.request('POST', public + 'claim', 429)

    # Turning off the binding gate must not turn off issuance authorization.
    config['invite']['invite_code_gate_enabled'] = False
    with env.server(config, root, base):
        ordinary.request('POST', init, 403, headers, {'count': 1})
        issuer.request('POST', init, 200, headers, {'count': 1})
