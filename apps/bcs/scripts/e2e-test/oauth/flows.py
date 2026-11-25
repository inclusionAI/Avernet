"""Live browser journeys. Assertions use HTTP outcomes, not store seeding."""
from harness import claims, jwt
from urllib.parse import parse_qs, urlencode, urlsplit

V1 = '/openapi/v1/auth'
LEGACY = '/auth'


def user(browser, base, token=None, expected=200, family=V1, extra=None):
    headers = dict(extra or {})
    if token is not None:
        headers['Cookie'] = 'bcs_session=' + token
    body, _, _ = browser.request('GET', base + family + '/user', expected, headers)
    return body.get('data', body)


def lifecycle(env, base):
    for provider in ('github', 'google', 'wechat', 'alipay'):
        a = env.browser()
        token = a.login(base, provider)
        identity = user(a, base)
        assert identity['provider'] == provider
        assert identity['avatar'] == f'/avatars/{provider}/alice.png'
        legacy = user(a, base, family=LEGACY)
        assert identity['user_id'] == legacy['user_id']
        assert identity['avatar'] == legacy['avatar']
        # Both facades share one session; refresh in either invalidates the old token.
        for family in (V1, LEGACY):
            _, headers, _ = a.request('POST', base + family + '/refresh', headers={'Origin': base})
            next_token = a.cookie()
            assert next_token != token and claims(next_token)['session_id'] == claims(token)['session_id']
            assert claims(next_token)['revision'] == claims(token)['revision'] + 1
            user(env.browser(), base, token, 401)
            user(a, base)
            token = next_token
        # A new login supersedes old sessions, but stale logout cannot revoke it.
        b = env.browser()
        latest = b.login(base, provider)
        user(env.browser(), base, token, 401)
        a.request('POST', base + LEGACY + '/logout', headers={'Origin': base})
        user(b, base)
        b.request('POST', base + V1 + '/logout', headers={'Origin': base})
        assert b.cookie() is None
        user(env.browser(), base, latest, 401)
        # Logout by old revision revokes a refreshed descendant of the SAME sid.
        stale = a.login(base, provider, family=LEGACY)
        a.request('POST', base + V1 + '/refresh', headers={'Origin': base})
        rotated = a.cookie()
        env.browser().request('POST', base + V1 + '/logout', headers={'Origin': base, 'Cookie': 'bcs_session=' + stale})
        user(env.browser(), base, rotated, 401)
        # Already-revoked logout remains idempotent.
        env.browser().request('POST', base + V1 + '/logout', headers={'Origin': base, 'Cookie': 'bcs_session=' + stale})
    for family in (V1, LEGACY):
        env.browser().request('POST', base + family + '/logout')
    a = env.browser()
    a.login(base, user='nameless')
    assert user(a, base)['user_id']


def browser_binding(env, base):
    for family in (V1, LEGACY):
        a, b = env.browser(), env.browser()
        urls = a.urls(base, family)
        callback = a.authorize(next(p['url'] for p in urls if p['name'] == 'github'))
        before = env.stub.count('exchange')
        nonce = a.cookie('bcs_oauth_login')
        b.request('GET', callback, 400)
        assert env.stub.count('exchange') == before, 'foreign browser reached token exchange'
        a.request('GET', callback, 302)
        assert env.stub.count('exchange') == before + 1
        # Replay the original browser proof, not just a request without a cookie:
        # the consumed state and every sibling state must still be rejected.
        b.request('GET', callback, 400, {'Cookie': 'bcs_oauth_login=' + nonce})
        sibling = a.authorize(next(p['url'] for p in urls if p['name'] == 'google'))
        a.request('GET', sibling, 400, {'Cookie': 'bcs_oauth_login=' + nonce})
        assert env.stub.count('exchange') == before + 1
        # Rotating the challenge makes the prior batch unusable in this browser.
        old = a.authorize(a.urls(base, family)[0]['url'])
        fresh = a.authorize(a.urls(base, family)[0]['url'])
        a.request('GET', old, 400)
        a.request('GET', fresh, 302)
        # Missing code, wrong state/provider, then the valid callback must still work.
        callback = a.authorize(a.urls(base, family)[0]['url'])
        nonce = a.cookie('bcs_oauth_login')
        for cookie in ('bcs_oauth_login=', '__Host-bcs_oauth_login=' + nonce,
                       f'bcs_oauth_login={nonce}; bcs_oauth_login={nonce}'):
            b.request('GET', callback, 400, {'Cookie': cookie})
        url = urlsplit(callback)
        query = parse_qs(url.query)
        a.request('GET', url._replace(query=urlencode({'state': query['state'][0]})).geturl(), 400)
        a.request('GET', url._replace(query=urlencode({'state': 'wrong', 'code': 'wrong'})).geturl(), 400)
        a.request('GET', url._replace(path=family + '/callback/unlisted').geturl(), 404)
        a.request('GET', callback, 302)
        for hint, code in [('token-error', 'token_exchange_failed'), ('info-error', 'userinfo_request_failed')]:
            a.jar.clear()
            callback = a.authorize(next(p['url'] for p in a.urls(base, family) if p['name'] == 'github'), hint)
            body, headers, _ = a.request('GET', callback, 502 if family == V1 else 500)
            assert not a.cookie() and not a.cookie('bcs_oauth_login')
            assert any('Max-Age=0' in value for value in headers.get_all('Set-Cookie'))
            if family == V1:
                assert body['data']['error_code'] == code


def rejection_and_precedence(env, base, gateway_first=False):
    a = env.browser()
    token = a.login(base)
    for family in (V1, LEGACY):
        for endpoint in ('refresh', 'logout'):
            for headers in ({}, {'Origin': 'https://untrusted.invalid'}, {'Origin': 'null'}):
                a.request('POST', base + family + '/' + endpoint, 403, headers)
        assert a.cookie() == token
    for value in ('', 'garbage', token + '; bcs_session=' + token):
        user(env.browser(), base, value, 401)
        env.browser().request('POST', base + V1 + '/refresh', 401,
            {'Origin': base, 'Cookie': 'bcs_session=' + value})
        env.browser().request('POST', base + V1 + '/logout', 401,
            {'Origin': base, 'Cookie': 'bcs_session=' + value})
    env.browser().request('POST', base + V1 + '/refresh', 401)
    # Signed-but-unbound, foreign-environment, expired and legacy claim shapes.
    original = claims(token)
    variants = [dict(original, env='foreign'), dict(original, exp=1), dict(original, session_id='foreign'),
                dict(original, revision=original['revision'] + 1), dict(original, sub='unknown'),
                {k: v for k, v in original.items() if k not in {'env', 'session_id', 'revision'}}]
    for payload in variants:
        user(env.browser(), base, jwt(payload, env.key), 401)
    mixed = {'x-avernet-principal': env.gateway()}
    identity = user(a, base, extra=mixed)
    assert identity['provider'] == ('gateway' if gateway_first else 'github')
    if gateway_first:
        user(a, base, extra={'x-avernet-principal': 'invalid'}, expected=401)
        assert user(env.browser(), base, 'invalid', extra=mixed)['provider'] == 'gateway'
    else:
        assert user(a, base, extra={'x-avernet-principal': 'invalid'})['provider'] == 'github'
        user(env.browser(), base, 'invalid', 401, extra=mixed)
    # A real protected read and write exercise provenance and the CSRF middleware.
    groups = '/openapi/v1/collaboration/groups'
    a.request('GET', base + groups)
    a.request('POST', base + groups, 403, body={})
    a.request('POST', base + groups, (400, 422), {'Origin': base, 'Content-Type': 'application/json'}, {})
    a.request('GET', base + groups, 403, {'x-bcs-expected-user': 'not-this-user'})
    # Legacy user lookup also goes through strict verification.
    identity = user(a, base)
    a.request('GET', base + LEGACY + '/user/' + identity['user_id'])
    a.request('GET', base + LEGACY + '/user/somebody-else', 403)


def compatibility(env):
    # No api.auth: public protected routes retain gateway-only trust, while
    # both login facades share the secured OAuth lifecycle and CORS origins.
    config, root, base = env.config('compat')
    config['cors']['allowed_origins'] += ['*', 'null']
    with env.server(config, root, base):
        a = env.browser()
        first = a.login(base)
        identity = user(a, base)
        assert user(a, base, family=LEGACY)['user_id'] == identity['user_id']
        # Restoring current-user lookup must not open ordinary protected APIs.
        a.request('GET', base + '/openapi/v1/collaboration/groups', 401)
        assert identity['provider']
        a.request('POST', base + V1 + '/refresh', headers={'Origin': base})
        user(env.browser(), base, first, 401, family=LEGACY)
        user(a, base, family=LEGACY)
        a.request('POST', base + V1 + '/logout', headers={'Origin': base})
        user(a, base, expected=401, family=LEGACY)
        # Compatibility current-user uses the legacy chain, not Gateway.
        user(a, base, extra={'x-avernet-principal': env.gateway()}, expected=401)
    # Gateway-only explicitly closes the V1 login family but leaves /user usable.
    config, root, base = env.config('gateway')
    with env.server(config, root, base):
        a = env.browser()
        for method, path in [('GET', '/url'), ('GET', '/callback/github?state=x&code=x'),
                             ('POST', '/refresh'), ('POST', '/logout')]:
            body, _, _ = a.request(method, base + V1 + path, 404)
            assert body['data']['error_code'] == 'auth_not_configured'
        assert user(a, base, extra={'x-avernet-principal': env.gateway()})['provider'] == 'gateway'
    # Exact chain ordering is exercised through real signed gateway credentials.
    config, root, base = env.config(chain=['gateway', 'google', 'github', 'wechat', 'alipay'])
    config['api']['auth']['gateway'].update(issuers=['gateway', 'backend'], audience='bcs', key_id='bare')
    with env.server(config, root, base):
        rejection_and_precedence(env, base, gateway_first=True)
    # Valid legacy session for a disabled provider must not authenticate V1.
    config, root, base = env.config(chain=['google'])
    with env.server(config, root, base):
        a = env.browser()
        a.login(base, 'github', family=LEGACY)
        user(a, base, expected=401)  # All sources are NotApplicable.
        before = a.cookie()
        body, headers, _ = a.request('POST', base + V1 + '/refresh', 403, {'Origin': base})
        assert body['data']['error_code'] == 'session_forbidden'
        assert not headers.get_all('Set-Cookie') and a.cookie() == before
        user(a, base, family=LEGACY)
        a.request('POST', base + LEGACY + '/refresh', headers={'Origin': base})
        assert a.cookie() != before  # Legacy selection is independent.
        a.request('POST', base + V1 + '/logout', headers={'Origin': base})
        user(a, base, expected=401, family=LEGACY)
        b = env.browser()
        b.login(base, 'google')
        assert user(b, base)['provider'] == 'google'
        # Trusted origins on the explicit chain are not inferred from CORS.
        config['api']['auth']['trusted_browser_origins'] = ['https://other.example']
    with env.server(config, root, base):
        b = env.browser()
        b.login(base, 'google')
        b.request('POST', base + V1 + '/refresh', 403, {'Origin': base})
        b.request('POST', base + V1 + '/refresh', headers={'Origin': 'https://other.example'})


def concurrent_refresh(env, base):
    from concurrent.futures import ThreadPoolExecutor
    from http.cookies import SimpleCookie
    from threading import Barrier

    a = env.browser()
    token = a.login(base)
    barrier = Barrier(2)

    def refresh(family):
        browser = env.browser()
        barrier.wait(timeout=10)
        return browser.request('POST', base + family + '/refresh', (200, 401),
            {'Origin': base, 'Cookie': 'bcs_session=' + token})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(refresh, (V1, LEGACY)))
    assert sorted(status for _, _, status in results) == [200, 401], 'one CAS rotation must win'
    _, headers, _ = next(result for result in results if result[2] == 200)
    cookie = SimpleCookie()
    cookie.load(headers['Set-Cookie'])
    rotated = cookie['bcs_session'].value
    user(env.browser(), base, rotated)
    user(env.browser(), base, token, 401)
    # Even when the old refresh loses, its logout revokes the descendant.
    env.browser().request('POST', base + V1 + '/logout', headers={
        'Origin': base, 'Cookie': 'bcs_session=' + token})
    user(env.browser(), base, rotated, 401)


def pending_capacity(env):
    config, root, base = env.config()
    with env.server(config, root, base, memory=True):
        original = env.browser()
        urls = original.urls(base)
        assert len(urls) == 4
        # Four provider states per batch: fill the documented 4096-state limit
        # using the public URL endpoint, without callbacks or store access.
        filler = env.browser()
        for _ in range(1023):
            filler.urls(base)
        before = env.stub.count('exchange')
        body, headers, _ = filler.request('GET', base + V1 + '/url', 503)
        assert body['data']['error_code'] == 'auth_dependency_unavailable'
        assert not headers.get_all('Set-Cookie'), 'full store must not issue a challenge'
        assert env.stub.count('exchange') == before
        # Saturation must not evict the first browser's live pending login.
        callback = original.authorize(next(p['url'] for p in urls if p['name'] == 'github'))
        original.request('GET', callback, 302)
        user(original, base)
        filler.urls(base)  # consuming the original batch released its capacity
