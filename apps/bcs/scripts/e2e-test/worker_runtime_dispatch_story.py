"""Finite Dispatch and TaskFeed stories through the public Worker companion."""


def run(h, checks):
    def call(action, **fields):
        return h.call('dispatch', action=action, **fields)

    def checkpoint(sid):
        return h.call('checkpoint', sid=sid)['checkpoint']

    def current(did):
        return call('get', did=did)

    def supervised_unknown(sid, tid, did, phase, cancellation=None):
        before = current(did)
        consumed = checkpoint(sid)['consumed']
        h.until(lambda: current(did)['revision'] != before['revision'],
                'Unknown dispatch did not persist a supervisor recheck')
        observed = current(did)
        assert observed['view']['execution']['phase'] == phase, observed
        assert observed['view']['execution']['outcome'] is None, observed
        assert observed['terminal_evidence'] is None, observed
        if cancellation is not None:
            assert observed['view']['execution']['cancellation']['kind'] == cancellation, observed
        assert call('due', did=did), 'Unknown remote obligation disappeared'
        assert checkpoint(sid)['consumed'] == consumed, 'Unknown invented a terminal input'
        calls = h.call('tick')['runner_calls']
        saved = h.call('archive', tid=tid)
        call('holder', did=did, id=did + '.resume-finish', operation='finish',
             evidence=did + '.definitive-error')
        h.until(lambda: checkpoint(sid)['consumed'] != consumed,
                'definitive late dispatch result did not reach Worker')
        assert h.call('tick')['runner_calls'] == calls
        assert h.call('archive', tid=tid) == saved
        h.until(lambda: not call('due', did=did), 'terminal return did not settle due record')

    h.until(lambda: h.call('runtime')['ready'], 'Worker not ready for Dispatch story')
    sid, tid = 's0001.interaction', 's0001.interaction.task'
    did = tid + '.dispatch'
    h.call('admit', sid=sid, tid=tid, id=sid + '.start', payload={'mode': 'interaction'})
    h.status(tid, 'input_required')
    start = current(did)
    assert start['view']['execution']['phase'] == 'created'
    view = call('claim_send', did=did, accepted=True)
    assert view['execution']['phase'] == 'accepted'
    original_epoch = view['execution']['holder']['epoch']
    reply = dict(sid=sid, tid=tid, id=sid + '.reply', payload='approved')
    assert call('reply', **reply).startswith('Accepted')
    assert call('reply', **reply).startswith('Duplicate')
    h.status(tid, 'working')
    h.until(lambda: len(current(did)['view']['control']['pending_input']) == 1,
            'HumanReply was not forwarded to Dispatch')
    pending = current(did)['view']['control']['pending_input']
    assert pending[0]['payload'] == {'kind': 'inline', 'value': 'approved'}
    assert h.call('snapshot', tid=tid)['content']['interactions'] == []
    calls = h.call('tick')['runner_calls']
    assert call('reply', **reply).startswith('Duplicate')
    h.call('tick', count=10)
    assert h.call('tick')['runner_calls'] == calls
    checks.append('human_reply_dispatch_input_once')

    view = call('holder', did=did, id=did + '.resume-input', operation='confirm_input', seq=1)
    assert view['execution']['holder']['epoch'] > original_epoch
    assert view['execution']['confirmed_input_through'] == 1
    assert view['control']['pending_input'] == []
    h.call('cancel', sid=sid, tid=tid, id=sid + '.cancel')
    h.status(tid, 'canceled')
    view = call('holder', did=did, id=did + '.resume-cancel', operation='cancel_unknown')
    assert view['control']['cancel'] is not None
    assert view['execution']['cancellation']['kind'] == 'outcome_unknown'
    supervised_unknown(sid, tid, did, 'accepted', 'outcome_unknown')
    archived = call('list', sid=sid)['items']
    assert len(archived) == 1 and archived[0]['content']['status'] == 'canceled'
    checks.append('holder_input_acceptance_and_cancel_unknown')

    sid, tid = 's0001.stream', 's0001.stream.task'
    did = tid + '.dispatch'
    h.call('admit', sid=sid, tid=tid, id=sid + '.start', payload={'mode': 'stream'})
    h.status(tid, 'working')
    view = call('claim_send', did=did, accepted=False)
    assert view['execution']['phase'] == 'sending'
    restored = h.call('restore', tid=tid)
    assert restored['kind'] == 'live'
    old_cursor = restored['cursor']
    assert call('validate_cursor', cursor=old_cursor) == 'Valid'
    call('validate_cursor', cursor=old_cursor, wrong_scope=True, error='task_scope')
    cursor = old_cursor
    for seq, delta in [(1, 'a' * 33_000), (2, 'b' * 33_000)]:
        call('agent', tid=tid, seq=seq, payload=delta)
        h.until(lambda: checkpoint(sid)['dispatch_seq'].get(did, {}).get('sequence') == seq,
                f'agent delta {seq} did not commit')
        # Stream append is an outbox step after the checkpoint commit.
        delivered = {'kind': 'pending'}
        def next_events():
            nonlocal delivered
            delivered = call('follow', cursor=cursor)
            return delivered['kind'] == 'events'
        h.until(next_events, f'live follow did not deliver delta {seq}')
        assert any(event['body'].get('update', {}).get('delta') ==
                   {'kind': 'inline', 'value': delta} for event in delivered['items']), delivered
        cursor = delivered['next']
    h.until(lambda: h.call('snapshot', tid=tid)['snapshot_event_id'] == checkpoint(sid)['last_event_id'],
            'full snapshot did not cover the latest appended delta')
    snapshot = h.call('snapshot', tid=tid)
    assert snapshot['content']['snapshot']['kind'] == 'ref'
    expected = {'format_version': 1, 'units': [
        {'source': 'answer', 'artifact_id': 'answer', 'class': 'output',
         'content': 'seed' + 'a' * 33_000 + 'b' * 33_000}]}
    assert h.call('restore', tid=tid)['view']['content']['snapshot'] == {'kind': 'inline', 'value': expected}
    assert call('trim', sid=sid, keep_from=snapshot['snapshot_event_id']) in ('Trimmed', 'AlreadyTrimmed')
    assert call('validate_cursor', cursor=old_cursor) == 'Gap'
    assert call('follow', cursor=old_cursor) == {'kind': 'gap'}
    assert call('validate_cursor', cursor=cursor) == 'Valid'
    assert h.call('restore', tid=tid)['view']['content']['snapshot'] == {'kind': 'inline', 'value': expected}
    checks.append('live_follow_snapshot_trim_gap_restore')

    calls = h.call('tick')['runner_calls']
    h.call('cancel', sid=sid, tid=tid, id=sid + '.cancel')
    h.status(tid, 'canceled')
    assert h.call('tick')['runner_calls'] == calls
    terminal = call('follow', cursor=cursor)
    assert terminal['kind'] == 'events'
    assert any(event['body']['kind'] == 'terminal' for event in terminal['items'])
    assert h.call('restore', tid=tid)['kind'] == 'final'
    supervised_unknown(sid, tid, did, 'sending')
    checks.append('sending_unknown_late_evidence_without_rerun')
