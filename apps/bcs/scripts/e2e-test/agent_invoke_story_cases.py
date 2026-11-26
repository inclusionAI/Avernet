"""Management and dispatch journeys through a real production-composed server."""
import copy
import time
import uuid
from agent_invoke_story import PREFIXES


def request(s, prefix, target=None, **options):
    return s.mutation(prefix, '/requests', {'from_bot_id': s.source, 'to_bot_id': target or s.target,
        'reason': 'source authority request'}, subject=s.source_owner, **options)


def decision(s, prefix, receipt, action, **options):
    item = receipt['current']['request']
    suffix = s.query('/requests/' + item['request_id'] + '/' + action)
    return s.mutation(prefix, suffix, {'expected_version': item['version'], 'reason': action}, **options)


def grant(s, prefix, target=None, **options):
    bot = target or s.target
    rows = s.request('GET', prefix + s.query('/grants', bot=bot, status='revoked'))['result']['items']
    versions = [g['version'] for g in rows if g['from_bot_id'] == s.source]
    return s.mutation(prefix, '/grants', s.grant_body(bot, max(versions, default=0)), **options)


def revoke(s, prefix, receipt, target=None, **options):
    g = receipt['current']['grant']
    return s.mutation(prefix, s.query('/grants/' + str(g['edge_id']) + '/revoke', bot=target),
        {'expected_version': g['version'], 'reason': 'explicit revoke'}, **options)


def queries(s, prefix, approved):
    s.request('GET', prefix + s.query('/grants', status='approved', limit=1))
    s.request('GET', prefix + s.query('/grants', bot=s.source, direction='outbound'), subject=s.source_owner)
    s.request('GET', prefix + s.query('/grants/' + str(approved['edge_id'])))
    s.request('GET', prefix + s.query('/degrees', direction=None))
    page = s.request('GET', prefix + s.query('/audit', peer=s.source, limit=1))['result']
    assert page['items'] and page['next_cursor']
    follow = s.request('GET', prefix + s.query('/audit', peer=s.source, limit=1, cursor=page['next_cursor']))['result']
    assert follow['items'][0]['id'] != page['items'][0]['id'], 'cursor repeated an audit row'
    for action in ['request', 'grant', 'approve', 'reject', 'cancel', 'revoke',
                   'qualification_publish', 'qualification_invalidate', 'delegation_publish']:
        s.request('GET', prefix + s.query('/audit', peer=s.source, action=action))
    s.request('GET', prefix + s.query('/audit', peer=s.source, limit=1, cursor='garbage'), status=400)
    s.request('GET', prefix + s.query('/audit', peer=s.targets[1], limit=1, cursor=page['next_cursor']), status=400)


def direct(s, prefix):
    before = len(s.peer.requests)
    accepted = s.invoke(prefix)
    assert accepted.get('run_id'), 'no real A2A run accepted'
    for _ in range(100):
        with s.peer.lock:
            delivered = len(s.peer.requests) > before
        if delivered:
            break
        time.sleep(0.05)
    else:
        raise AssertionError('approved Agent invocation never reached A2A peer')
    assert s.peer.legacy_requests == 0, 'Agent invocation used legacy Provider webhook'


def lifecycle(s, prefix):
    before = len(s.peer.requests)
    s.invoke(prefix, status=403)
    assert len(s.peer.requests) == before, 'unapproved invocation reached peer'
    s.mutation(prefix, '/grants', s.grant_body(), subject=s.source_owner, status=403)
    # Cancel, reject and approve are separate real command paths.
    pending = request(s, prefix)
    s.request('GET', prefix + s.query('/requests/' + pending['current']['request']['request_id']))
    s.request('GET', prefix + s.query('/requests', bot=s.source, direction='outbound'), subject=s.source_owner)
    decision(s, prefix, pending, 'cancel', status=403)
    item = pending['current']['request']
    cancelled = s.mutation(prefix, s.query('/requests/' + item['request_id'] + '/cancel', bot=s.source, direction='outbound'),
        {'expected_version': item['version'], 'reason': 'cancel'}, subject=s.source_owner)
    assert cancelled['current']['request']['status'] == 'cancelled'
    pending = request(s, prefix)
    rejected = decision(s, prefix, pending, 'reject')
    assert rejected['current']['request']['status'] == 'rejected'
    decision(s, prefix, pending, 'approve', status=409)
    pending = request(s, prefix)
    decision(s, prefix, pending, 'approve', subject=s.source_owner, status=403)
    approved = decision(s, prefix, pending, 'approve')
    queries(s, prefix, approved['current']['grant'])
    direct(s, prefix)
    revoked = revoke(s, prefix, approved)
    assert revoked['current']['grant']['status'] == 'revoked'
    s.invoke(prefix, status=403)
    key = uuid.uuid4().hex
    g = grant(s, prefix, key=key)
    body = s.grant_body(version=revoked['current']['grant']['version'])
    assert s.mutation(prefix, '/grants', body, key=key)['replayed']
    s.mutation(prefix, '/grants', {**body, 'reason': 'different payload'}, key=key, status=409)
    # Qualification loss durably revokes; restoration and old receipts cannot resurrect.
    generation = s.facts[s.target]['eligibility_generation']
    s.fact(s.target, eligible=False, eligibility_generation=generation + 1)
    s.invoke(prefix, status=403)
    s.fact(s.target, eligible=True, eligibility_generation=generation + 2)
    replay = s.mutation(prefix, '/grants', body, key=key)
    assert replay['current']['grant']['status'] == 'revoked'
    s.invoke(prefix, status=403)
    g = grant(s, prefix)
    direct(s, prefix)
    revoke(s, prefix, g)
    # Closed writer/runtime preserve authorized historical reads.
    s.gates(False, False)
    s.request('GET', prefix + s.query('/grants', status='revoked'))
    s.invoke(prefix, status=503)
    request(s, prefix, status=503)
    s.gates(True, True)


def governance(s, prefix):
    pair = {'principal_id': 'fixture-governor', 'from': s.source, 'to': s.target}
    s.request('GET', prefix + s.query('/grants', peer=s.source), actor='app', status=403)
    s.publish('delegation', **pair, expected_revision=0,
        actions=['request', 'grant', 'approve', 'reject', 'cancel', 'revoke', 'query'])
    for suffix in ['/grants', '/requests', '/audit']:
        s.request('GET', prefix + s.query(suffix, peer=s.source), actor='app')
    s.request('GET', prefix + s.query('/degrees', peer=s.source, direction=None), actor='app', status=403)
    s.publish('delegation', principal_id='fixture-governor', **{'from': s.target, 'to': s.source},
              expected_revision=0, actions=['query'])
    s.request('GET', prefix + s.query('/degrees', peer=s.source, direction=None), actor='app')
    g = grant(s, prefix, actor='app')
    suffix = s.query('/grants/' + str(g['current']['grant']['edge_id']) + '/revoke', peer=s.source)
    s.mutation(prefix, suffix, {'expected_version': g['current']['grant']['version'], 'reason': 'delegated stop'}, actor='app')
    s.publish('delegation', **pair, expected_revision=1, actions=[])
    s.request('GET', prefix + s.query('/audit', peer=s.source), actor='app', status=403)


def quotas(s, prefix):
    active = [grant(s, prefix, target=t) for t in s.targets[:3]]
    grant(s, prefix, target=s.targets[3], status=429)
    page = s.request('GET', prefix + s.query('/grants', bot=s.source, direction='outbound', limit=1), subject=s.source_owner)['result']
    assert len(page['items']) == 1 and page['next_cursor']
    s.request('GET', prefix + s.query('/grants', bot=s.source, direction='outbound', limit=1,
                                    cursor=page['next_cursor']), subject=s.source_owner)
    for t, g in zip(s.targets, active):
        revoke(s, prefix, g, target=t)
    pending = [request(s, prefix, target=t) for t in s.targets[:3]]
    request(s, prefix, target=s.targets[3], status=429)
    page = s.request('GET', prefix + s.query('/requests', bot=s.source, direction='outbound', limit=1), subject=s.source_owner)['result']
    assert page['next_cursor']
    s.request('GET', prefix + s.query('/requests', bot=s.source, direction='outbound', limit=1,
                                    cursor=page['next_cursor']), subject=s.source_owner)
    for p in pending:
        item = p['current']['request']
        s.mutation(prefix, s.query('/requests/' + item['request_id'] + '/cancel', bot=s.source, direction='outbound'),
            {'expected_version': item['version'], 'reason': 'quota cleanup'}, subject=s.source_owner)


def queued_revoke(s, prefix):
    g = grant(s, prefix)
    policy = s.request('GET', '/admin/message-delivery/policy', actor=None)
    changed = copy.deepcopy(policy['policy'])
    changed['flow_enabled']['direct_a2a'] = True
    changed['defaults'].update(mode='enforce', min_send_interval_ms=1, max_running=8)
    changed['pause_dispatch'] = True
    s.request('PUT', '/admin/message-delivery/policy', {'expected_version': policy['version'], 'policy': changed}, actor=None)
    before = len(s.peer.requests)
    accepted = s.invoke(prefix)
    rows = s.sql('SELECT status FROM bcs_message_deliveries WHERE run_id=?', (accepted['run_id'],))
    assert rows == [('queued',)], 'real Direct queue was not paused before revoke'
    revoke(s, prefix, g)
    current = s.request('GET', '/admin/message-delivery/policy', actor=None)
    changed['pause_dispatch'] = False
    s.request('PUT', '/admin/message-delivery/policy', {'expected_version': current['version'], 'policy': changed}, actor=None)
    for _ in range(200):
        rows = s.sql('SELECT status FROM bcs_message_deliveries WHERE run_id=?', (accepted['run_id'],))
        if rows and rows[0][0] in ('failed', 'cancelled', 'expired'):
            break
        time.sleep(0.05)
    else:
        raise AssertionError('queued revoked invocation never reached its denied terminal state')
    assert len(s.peer.requests) == before, 'revoked queued invocation reached the peer'
    # Also prove the positive queue path, not just rejection before transport.
    g = grant(s, prefix)
    direct(s, prefix)
    revoke(s, prefix, g)


def errors(s, prefix):
    for suffix in ['/grants', '/requests', '/degrees', '/audit', '/grants/1', '/requests/' + str(uuid.uuid4())]:
        path = prefix + s.query(suffix, direction=None if suffix == '/degrees' else 'inbound',
                                peer=s.source if suffix == '/audit' else None)
        s.request('GET', path, actor=None, status=401)
        s.request('GET', path, actor='bot', status=403)
        s.request('GET', path, subject='foreign-user', status=403)
        s.request('GET', path, tenant='foreign-tenant', status=404)
    for suffix, body in [('/grants', s.grant_body()), ('/requests', {
            'from_bot_id': s.source, 'to_bot_id': s.target, 'reason': 'request'})]:
        s.mutation(prefix, suffix, body, actor=None, status=401)
        s.mutation(prefix, suffix, body, actor='bot', status=403)
        s.mutation(prefix, suffix, body, actor='app', subject=8, status=403)
        s.mutation(prefix, suffix, {**body, 'eligible': True}, status=400)
        s.mutation(prefix, suffix, {**body, 'from_bot_id': s.target}, status=400)
        s.mutation(prefix, suffix, {**body, 'reason': ''}, status=400)
        s.mutation(prefix, suffix, body, key='a' * 129, status=400)
        s.request('POST', prefix + suffix, body, status=400)
    for suffix in ['/grants', '/requests', '/degrees', '/audit']:
        s.request('GET', prefix + s.query(suffix, limit=0), status=400)
        s.request('GET', prefix + s.query(suffix) + '&bot_id=duplicate', status=400)
        s.request('GET', prefix + s.query(suffix) + '&scope=untrusted', status=400)
        s.request('PUT', prefix + suffix, {}, status=405)
    s.request('GET', prefix + s.query('/grants/0'), status=400)
    s.request('GET', prefix + s.query('/grants/9223372036854775807'), status=404)
    for body in [{'timeout_ms': 0}, {'timeout_ms': 30001}, {'session_id': 'a2a-test:forbidden'},
                 {'message': ''}, {'to_bot_id': s.source}, {'to_bot_id': 'human_not-an-agent'},
                 {'from_bot_id': s.source}, {'scope': 'untrusted'}]:
        s.invoke(prefix, status=400, **body)
    s.request('POST', prefix + '/invocations', s.invoke_body(), actor=None, status=401)
    s.request('POST', prefix + '/invocations', s.invoke_body(), status=403)
    s.request('POST', prefix + '/invocations', s.invoke_body(), actor='app', status=403)
    s.request('POST', prefix + '/invocations', s.invoke_body(), actor='bot', subject='wrong-code', status=403)
    s.request('GET', prefix + '/invocations', status=405)
    s.request('GET', prefix + '/not-an-alias', status=404)


def exercise(s):
    for prefix in PREFIXES:
        lifecycle(s, prefix)
        s.scenarios.append('lifecycle:' + prefix.split('/')[1])
        errors(s, prefix)
        s.scenarios.append('authentication-validation:' + prefix.split('/')[1])
    governance(s, PREFIXES[0])
    s.scenarios.append('governance')
    quotas(s, PREFIXES[1])
    s.scenarios.append('active-pending-quotas')
    queued_revoke(s, PREFIXES[0])
    s.scenarios.append('queued-revoke-and-dispatch')
