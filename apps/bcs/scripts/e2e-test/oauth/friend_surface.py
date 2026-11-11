"""Friend-connect surface E2E against the in-memory composition root.

The OAuth companion's memory-mode server is the only e2e runtime that wires
the edge-permission in-memory stores (`bcs_edge_permission_store::memory`)
behind the real `DbConnectService`, the legacy `/friends/*` facade and the v1
openapi friendship surface. These scenarios drive the new friend framework
end to end over live HTTP — request -> pending inbox -> accept/reject ->
edges -> list -> revoke — so the in-memory store implementations, the
`EdgeConnectFriendService` facade, the v1 invitation/friendship application
and the friendship routes are all exercised with real assertions, never
shape-only probes.

Run standalone against a locally built companion for quick iteration:
    python3 scripts/e2e-test/oauth/friend_surface.py \
        --binary target/debug/bcs-oauth-e2e
"""
from urllib.parse import urlencode

def gateway_headers(env, user):
    return {'x-avernet-principal': env.gateway(user)}


def bot_headers(token):
    return {'Authorization': 'Bearer ' + token}


def req_json(browser, method, url, expected=200, headers=None, body=None):
    """browser.request with an explicit JSON content type when a body is sent."""
    merged = dict(headers or {})
    if body is not None:
        merged.setdefault('Content-Type', 'application/json')
    return browser.request(method, url, expected, headers=merged, body=body)


def login_user(env, base, name):
    """Log a human in through the real OAuth flow and return (browser, user_id).

    The same identity then carries the gateway-principal header the v1 openapi
    surface authenticates, so register-bot ownership and v1 caller checks agree
    on one human id.
    """
    browser = env.browser()
    browser.login(base, user=name)
    body, _, _ = req_json(browser, 'GET', base + '/me')
    user_id = data_of(body).get('user_id')
    assert user_id, f'/me did not resolve a user id for {name}: {body!r}'
    return browser, user_id


def register_bot(browser, base, name):
    """Mint a register token for the logged-in owner and register a fresh bot."""
    body, _, _ = req_json(browser, 'GET', base + '/register/token')
    token = data_of(body).get('token')
    assert token, f'no register token for {name}: {body!r}'
    query = urlencode({'token': token, 'bot-name': name})
    body, _, _ = req_json(browser, 'POST', base + '/register?' + query)
    bot_uuid, bot_token = body.get('bot_uuid'), body.get('bot_token')
    assert bot_uuid and bot_token, f'registration of {name} did not return bot credentials: {body!r}'
    return bot_uuid, bot_token


def data_of(body):
    data = body.get('data', body) if isinstance(body, dict) else body
    return data


def items_of(body):
    data = data_of(body)
    if isinstance(data, dict):
        return data.get('items', [])
    return data if isinstance(data, list) else []


def legacy_pending_request(browser, base, token, from_uuid):
    body, _, _ = req_json(browser, 'GET', base + '/friends/requests?direction=received&status=pending', headers=bot_headers(token))
    for request in items_of(body):
        if request.get('from_bot') == from_uuid and request.get('status') == 'pending':
            return request
    raise AssertionError(f'no pending legacy request from {from_uuid}: {body!r}')


def friend_connect_surface(env, base):
    """Drive the new friend framework full link on the in-memory server."""
    owner1_browser, owner1_id = login_user(env, base, 'friend-owner-1')
    owner2_browser, owner2_id = login_user(env, base, 'friend-owner-2')
    _stranger_browser, stranger_id = login_user(env, base, 'friend-stranger')

    alpha, alpha_token = register_bot(owner1_browser, base, 'friend-alpha')
    beta, beta_token = register_bot(owner2_browser, base, 'friend-beta')
    gamma, gamma_token = register_bot(owner2_browser, base, 'friend-gamma')
    delta, delta_token = register_bot(owner2_browser, base, 'friend-delta')
    epsilon, _epsilon_token = register_bot(owner2_browser, base, 'friend-epsilon')

    http = env.browser()
    legacy_accept_link(http, base, alpha, alpha_token, beta, beta_token)
    legacy_reject_link(http, base, alpha, alpha_token, gamma, gamma_token)
    v1_friendship_surface(http, base, alpha, gateway_headers(env, owner1_id),
                          delta, epsilon, gateway_headers(env, owner2_id),
                          gateway_headers(env, stranger_id))


def legacy_accept_link(browser, base, alpha, alpha_token, beta, beta_token):
    """Legacy /friends facade: create -> pending list -> 403 wrong caller
    -> receiver accept -> edge visible on both APIs -> duplicate/idempotent
    branches -> not-found target."""
    # Create: protected-strategy target keeps the request pending.
    body, _, _ = req_json(browser, 'POST', base + '/friends/request', expected=(200, 201),
        headers=bot_headers(alpha_token), body={'from_bot': alpha, 'to_bot': beta})
    created = data_of(body)
    assert created.get('status') == 'pending' and created.get('id'), \
        f'legacy create must stay pending on the memory store: {body!r}'
    request_id = created['id']
    assert body.get('success') is True

    # Duplicate while pending: the facade must surface the legacy 409
    # compatibility row (existing pending id, "already pending" wording).
    body, _, _ = req_json(browser, 'POST', base + '/friends/request', expected=200,
        headers=bot_headers(alpha_token), body={'from_bot': alpha, 'to_bot': beta})
    duplicate = data_of(body)
    assert duplicate.get('status') == 'pending' and duplicate.get('id') == request_id, \
        f'duplicate pending must echo the existing request id: {body!r}'
    assert 'pending' in str(duplicate.get('message', '')).lower(), body

    # Pending inbox over the legacy listing (connect store read, filtered).
    pending = legacy_pending_request(browser, base, beta_token, alpha)
    assert pending['id'] == request_id and pending['to_bot'] == beta, pending

    # The connect surface sees the same pending row (new framework listing).
    body, _, _ = req_json(browser, 'GET', base + '/collaboration/friend-connections/requests?direction=received',
        headers=bot_headers(beta_token))
    assert any(r.get('request_id') == request_id and r.get('status') == 'pending'
               for r in items_of(body)), body

    # Sender must not decide the request (receiver-only accept).
    body, _, _ = req_json(browser, 'POST', base + f'/friends/requests/{request_id}/accept', expected=403,
        headers=bot_headers(alpha_token), body={})
    assert 'accept' in str(data_of(body) or body).lower() or 'Forbidden' in str(body), body

    # Receiver accepts through the legacy route: edges are built on the
    # in-memory edge store and mirrored into the legacy friendship list.
    body, _, _ = req_json(browser, 'POST', base + f'/friends/requests/{request_id}/accept', expected=200,
        headers=bot_headers(beta_token), body={})
    assert body.get('success') is True, body

    body, _, _ = req_json(browser, 'GET', base + f'/collaboration/friend-connections?actor={beta}&actor_kind=bot',
        headers=bot_headers(beta_token))
    assert any(e.get('actor_id') == alpha or e.get('friend_id') == alpha
               for e in items_of(body)), body

    body, _, _ = req_json(browser, 'GET', base + f'/bots/{alpha}/friends',
                                 headers=bot_headers(alpha_token))
    assert beta in str(data_of(body)), body

    body, _, _ = req_json(browser, 'GET', base + '/friends/requests?direction=received&status=accepted',
        headers=bot_headers(beta_token))
    assert any(r.get('id') == request_id and r.get('status') == 'accepted'
               for r in items_of(body)), body

    # Already friends: idempotent create projects the synthetic legacy row.
    body, _, _ = req_json(browser, 'POST', base + '/friends/request', expected=200,
        headers=bot_headers(alpha_token), body={'from_bot': alpha, 'to_bot': beta})
    assert 'already' in str(body.get('message', '')).lower(), body

    # Unknown target stays the legacy 404 (visibility gate, before connect).
    req_json(browser, 'POST', base + '/friends/request', expected=404,
                    headers=bot_headers(alpha_token),
                    body={'from_bot': alpha, 'to_bot': 'friend-no-such-bot'})


def legacy_reject_link(browser, base, alpha, alpha_token, gamma, gamma_token):
    """Legacy /friends facade: reject is receiver-only and settles the
    pending row into the rejected projection without creating an edge."""
    body, _, _ = req_json(browser, 'POST', base + '/friends/request', expected=(200, 201),
        headers=bot_headers(alpha_token), body={'from_bot': alpha, 'to_bot': gamma})
    request_id = data_of(body)['id']
    assert data_of(body).get('status') == 'pending', body

    body, _, _ = req_json(browser, 'POST', base + f'/friends/requests/{request_id}/reject', expected=403,
        headers=bot_headers(alpha_token), body={})
    assert 'reject' in str(data_of(body) or body).lower() or 'Forbidden' in str(body), body

    body, _, _ = req_json(browser, 'POST', base + f'/friends/requests/{request_id}/reject', expected=200,
        headers=bot_headers(gamma_token), body={})
    assert body.get('success') is True, body

    body, _, _ = req_json(browser, 'GET', base + '/friends/requests?direction=received&status=rejected',
        headers=bot_headers(gamma_token))
    assert any(r.get('id') == request_id and r.get('status') == 'rejected'
               for r in items_of(body)), body

    body, _, _ = req_json(browser, 'GET', base + f'/collaboration/friend-connections?actor={gamma}&actor_kind=bot',
        headers=bot_headers(gamma_token))
    assert not any(e.get('actor_id') == alpha or e.get('friend_id') == alpha
                   for e in items_of(body)), body


def v1_friendship_surface(browser, base, alpha, owner1, delta, epsilon, owner2, stranger):
    """V1 openapi friendship surface: owner-scoped create/list/accept/reject
    plus the connect-backed delete, settled through the decided-request
    read-back projection."""
    v1 = base + '/openapi/v1/collaboration'

    # Create through the v1 surface as the owning human (authorized resource).
    body, _, _ = req_json(browser, 'POST', v1 + f'/bots/{alpha}/friend-requests', expected=201,
        headers=owner1, body={'to_bot_uuid': delta})
    created = data_of(body)
    request_id = created.get('request_id') or created.get('request_uuid')
    assert request_id and created.get('status') == 'pending', body
    assert created.get('from_bot_uuid') == alpha and created.get('to_bot_uuid') == delta, body

    # V1 listing is owner-scoped and paginated; the sent pending row is there.
    body, _, _ = req_json(browser, 'GET', v1 + f'/bots/{alpha}/friend-requests?direction=sent&status=pending&limit=10',
        headers=owner1)
    assert any(r.get('request_id') == request_id and r.get('status') == 'pending'
               for r in items_of(body)), body

    # A non-owner principal may not decide the request (403, facade deny).
    req_json(browser, 'POST', v1 + f'/friend-requests/{request_id}/accept', expected=403,
                    headers=stranger, body={})

    # Receiver-side owner accepts; the response is read back from the connect
    # store decision (accepted projection carries DB-managed timestamps).
    body, _, _ = req_json(browser, 'POST', v1 + f'/friend-requests/{request_id}/accept', expected=200,
        headers=owner2, body={})
    decided = data_of(body)
    assert decided.get('status') == 'accepted' and decided.get('request_id') == request_id, body
    assert decided.get('created_at', 0) > 0 and decided.get('updated_at', 0) >= decided['created_at'], body

    body, _, _ = req_json(browser, 'GET', v1 + f'/bots/{alpha}/friendships', headers=owner1)
    assert any(f.get('friend_bot_uuid') == delta or f.get('bot_uuid') == delta or
               f.get('friend_uuid') == delta for f in items_of(body)), body

    # V1 reject path on a fresh request: receiver-side owner rejects.
    body, _, _ = req_json(browser, 'POST', v1 + f'/bots/{alpha}/friend-requests', expected=201,
        headers=owner1, body={'to_bot_uuid': epsilon})
    reject_id = data_of(body).get('request_id') or data_of(body).get('request_uuid')
    assert reject_id, body
    body, _, _ = req_json(browser, 'POST', v1 + f'/friend-requests/{reject_id}/reject', expected=200,
        headers=owner2, body={})
    rejected = data_of(body)
    assert rejected.get('status') == 'rejected' and rejected.get('request_id') == reject_id, body

    # Connect-backed delete: unfriending revokes the same edges the new
    # surface reads.
    body, _, _ = req_json(browser, 'DELETE', v1 + f'/bots/{alpha}/friendships/{delta}', expected=200, headers=owner1)
    assert data_of(body).get('deleted') is True, body
    body, _, _ = req_json(browser, 'GET', v1 + f'/bots/{alpha}/friendships', headers=owner1)
    assert not any(f.get('friend_bot_uuid') == delta or f.get('bot_uuid') == delta or
                   f.get('friend_uuid') == delta for f in items_of(body)), body


def main():
    """Standalone driver: start one memory-mode companion and run the surface."""
    import argparse
    import tempfile
    from pathlib import Path
    from provider_stub import provider_stub
    from harness import Environment

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='bcs-friend-surface-') as directory:
        root = Path(directory)
        with provider_stub(root) as stub:
            env = Environment(str(Path(args.binary).resolve()), root, stub)
            config, data, base = env.config()
            with env.server(config, data, base, memory=True):
                friend_connect_surface(env, base)
            for check in env.checks:
                print(f"  {check.get('method', 'env'):6} {check.get('path', ''):80} {check.get('status', '')}")
    print('friend-connect surface (memory): all scenarios passed')


if __name__ == '__main__':
    main()
