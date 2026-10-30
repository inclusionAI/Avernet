"""Version-one Event Webhook reports projected from original frozen evidence.

Digests detect drift, not a malicious rewrite by the owner of the Run files.
Offline verification never resolves identities or contacts the old runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-EVENT-WEBHOOK-01'
SUITE = 'owned-local-event-webhook-v1'
V1_REQUIREMENTS = tuple('WEBHOOK.'+name for name in (
    'receiver-empty','local-principal','group-create','group-event','subscription-list',
    'subscription-read','subscription-test','test-event','subscription-disable','subscription-enable',
    'session-create','session-event','message-create','message-event','stream-order','deliveries-list',
    'delivery-read','subscription-delete','session-delete','group-delete'))
HTTP_IDS=tuple(V1_REQUIREMENTS[i] for i in (2,4,5,6,8,9,10,12,15,16,17,18,19))

SOURCES = ('event-binding.json','event-flow-report.json','bcs-config.json',
           'owned-bcs.json','local-fixtures.json','local-event.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-event-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Event Webhook evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Event Webhook evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Event Webhook evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def _sources(root):
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(root):
    binding = read(root/'event-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_user_receives_group_event_webhooks'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Event Webhook frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'event-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-event.json') if (root/'local-event.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Event Webhook final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Event Webhook runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_EVENT_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_EVENT_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'RETAINED' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


def _history_valid(case):
    proof=case.get('retention',{});group=case.get('group_id');subscription=case.get('subscription_id')
    events=proof.get('events',[]);deliveries=proof.get('deliveries',[])
    records=case.get('receiver',{}).get('records',[])
    expected={row.get('event_id') for row in records if row.get('event_type')!='event_subscription.test'}
    observed={row.get('event_id') for row in events}
    if (not 1<=len(events)<=32 or not 1<=len(deliveries)<=32 or expected!=observed
            or {row.get('event_id') for row in deliveries}!=observed
            or len({row.get('delivery_id') for row in deliveries})!=len(deliveries)
            or any(row.get('group_id')!=group or row.get('fanout_status')!='completed' for row in events)
            or any(row.get('subscription_id')!=subscription or row.get('status')!='succeeded'
                   or row.get('subscription_revision') not in (1,3) for row in deliveries)):return False
    detail=case.get('delivery_evidence',{});delivery=detail.get('delivery',{});event=detail.get('event',{})
    attempts=detail.get('attempts',[])
    return (delivery in deliveries and event in events and delivery.get('delivery_id')==case.get('delivery_id')
            and event.get('event_id')==delivery.get('event_id') and bool(attempts)
            and all(row.get('delivery_id')==case['delivery_id'] for row in attempts)
            and attempts[-1].get('result')=='success' and type(attempts[-1].get('http_status')) is int
            and 200<=attempts[-1]['http_status']<300
            and case.get('message_retained')==case.get('message_evidence') and bool(case.get('message_retained')))


def _business_evidence(root, binding, case):
    from urllib.parse import quote
    requirements=V1_REQUIREMENTS
    group=case.get('group_id');subscription=case.get('subscription_id');session=case.get('session_id');delivery=case.get('delivery_id')
    if (not all(isinstance(value,str) and value for value in (group,subscription,session,delivery))
            or len(set(case.get('session_ids',[])))!=2 or session not in case['session_ids']
            or case.get('unknown_operations')!=[] or case.get('binding_digest')!=fingerprint(binding)
            or case.get('principal_memory')!='CLEARED'):return False
    ops=case.get('operations',[]);observations=case.get('observations',{})
    if [row.get('operation') for row in ops]!=list(HTTP_IDS) or set(observations)!=set(requirements):return False
    ids=[]
    for row in ops:
        op=row['operation'];original=binding['source_binding']['requirements'][requirements.index(op)]
        path=original['path'].replace('${public_prefix}','/openapi/v1/collaboration')
        for name,value in (('group_id',group),('subscription_id',subscription),('session_id',session),('delivery_id',delivery)):
            path=path.replace('${'+name+'}',quote(value,safe=''))
        path=path.replace('$(urlencode "$group_id")',quote(group,safe=''))
        if (row.get('method')!=original['method'] or row.get('path')!=path or row.get('actor')!=original['actor']
                or row.get('http_status')!=(201 if op in (requirements[2],requirements[10]) else 200)
                or row.get('status')!='VERIFIED' or row.get('transitions')!=['INTENDED','VERIFIED']
                or row.get('observed')!=observations[op]):return False
        ids.append(row.get('request_id'))
    if any(not isinstance(value,str) or not value for value in ids) or len(set(ids))!=13:return False
    if (observations[requirements[0]].get('received')!=0 or observations[requirements[0]].get('armed') is not False
            or observations[requirements[1]].get('identity')!='OWNED_LOCAL_HUMAN_PRINCIPAL'
            or observations[requirements[1]].get('credential_persisted') is not False
            or observations[requirements[2]]!={'group_id':group,'subscription_id':subscription}
            or observations[requirements[4]]!={'subscription_id':subscription}
            or observations[requirements[6]]!={'delivered':True}
            or observations[requirements[10]]!={'session_id':session,'group_id':group}
            or observations[requirements[12]]!={'message_id':case.get('message_id'),'message_accepted':True,
                'content_sha256':hashlib.sha256(b'Webhook delivery verification').hexdigest()}
            or observations[requirements[15]]!={'delivery_id':delivery}
            or observations[requirements[16]]!={'delivery_id':delivery,'subscription_id':subscription}
            or observations[requirements[18]]!={'session_id':session,'delete_accepted':True}
            or observations[requirements[19]]!={'group_id':group,'delete_accepted':True}):return False
    for index,revision,status in ((5,1,'active'),(8,2,'disabled'),(9,3,'active'),(17,4,'deleted')):
        if observations[requirements[index]]!={'subscription_id':subscription,'revision':revision,'status':status}:return False
    receiver=case.get('receiver',{})
    records=receiver.get('records',[])
    if (receiver.get('state')!='CLOSED' or receiver.get('failed') is not False or receiver.get('buffered')!=0
            or receiver.get('validated')!=len(records) or receiver.get('received')!=len(records)
            or not 4<=len(records)<=32):return False
    event_ids=[];streams={}
    for record in records:
        if record.get('group_id')!=group or record.get('authorization','missing') is not None:return False
        sequence=record.get('sequence');key=record.get('stream_key')
        if type(sequence) is not int or sequence<1 or not isinstance(key,str):return False
        streams.setdefault(key,[]).append(sequence);event_ids.append(record.get('event_id'))
    if (len(set(event_ids))!=len(records) or any(not isinstance(value,str) or not value for value in event_ids)
            or any(seq!=sorted(set(seq)) for seq in streams.values())):return False
    for index,event_type in ((3,'group.created'),(7,'event_subscription.test'),(11,'session.created'),(13,'message.created')):
        evidence=observations[requirements[index]]
        if (evidence not in records or evidence.get('event_type')!=event_type
                or evidence.get('subject_id')!={3:group,7:subscription,11:session,13:case.get('logical_message_id')}[index]):return False
    order=observations[requirements[14]]
    if (not order.get('streams') or order.get('record_count',0)<4
            or any(key not in streams or seq!=streams[key][:len(seq)] for key,seq in order['streams'].items())):return False
    fixtures=case.get('fixture_state',{});before=fixtures.get('before')
    if not before or fixtures.get('after')!=before or fixtures.get('preserved') is not True:return False
    effects=case.get('effects',{})
    if (effects.get('group')!={'status':'DELETED','id':group}
            or effects.get('subscription')!={'status':'TOMBSTONED','id':subscription}
            or effects.get('explicit_session')!={'status':'DELETED','id':session}
            or effects.get('implicit_session',{}).get('status')!='DELETED_WITH_GROUP'
            or effects['implicit_session'].get('id') not in case['session_ids']
            or effects['implicit_session']['id']==session
            or effects.get('message')!={'status':'ACCEPTED_HISTORY_RETAINED','id':case.get('message_id'),'logical_message_id':case.get('logical_message_id')}):return False
    logical=case.get('message_evidence',{})
    if (case.get('message_before',{}).get('matching_messages')!=0 or logical.get('message_id')!=case.get('logical_message_id')
            or not logical.get('message_id') or logical.get('group_id')!=group or logical.get('session_id') not in case['session_ids']
            or logical.get('sender_id')!=case['message_before'].get('sender_id')
            or logical.get('identity_kind')!='PERSISTED_LOGICAL_MESSAGE_NOT_LEGACY_RESPONSE_ID'
            or logical.get('content_sha256')!=observations[requirements[12]]['content_sha256']):return False
    retention=case.get('retention',{})
    return (retention.get('group_id')==group and retention.get('subscription_id')==subscription
            and retention.get('group_present') is False and retention.get('session_count')==0
            and retention.get('subscription',{}).get('status')=='deleted'
            and retention['subscription'].get('revision')==4
            and _history_valid(case)
            and case.get('resource_disposition')=='GROUP_SESSIONS_DELETED_EVENT_HISTORY_RETAINED')


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': projected.get('requirements', [{'id': name, 'status': 'NOT_RUN'} for name in V1_REQUIREMENTS]),
            'reports': {'expected_assertion_count': 20, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'EXACT_GROUP_SESSIONS_REMOVED_SUBSCRIPTION_TOMBSTONED_HISTORY_RETAINED',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'external engine completion', 'remote Webhook reachability', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_event(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Event Webhook source changed before report publication')
    _require(read(root/'event-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Event Webhook publication must follow actual runtime finalization')
    pending = root/'event-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'event-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'event-completion.json') == completion_digest,
             'Event Webhook source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_event_report(root: Path):
    _require(not root.is_symlink(), 'Event Webhook report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'event-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Event Webhook publication is incomplete')
        completion = read(root/'event-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Event Webhook completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Event Webhook evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'event-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Event Webhook evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Event Webhook completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
