"""Version-one delivery-policy reports projected from frozen evidence.

Digests detect drift, not a malicious rewrite by the owner of the
Run files. Offline verification never resolves identities or
contacts the old runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-DELIVERY-POLICY-01'
SUITE = 'owned-local-delivery-policy-v1'
PARENT = 'story_human_manages_delivery_policy'
PATH = '/admin/message-delivery/policy'
V1_REQUIREMENTS = tuple('DELIVERY.'+name for name in (
    'human-read','bot-read-denied','bot-write-denied','denied-preserves',
    'cas-update','readback','stale-rejected','conflict-preserves','restore','restored'))
V1_METHODS = ('GET','GET','PUT','GET','PUT','GET','PUT','GET','PUT','GET')
V1_STATUSES = (200,403,403,200,200,200,409,200,200,200)
CONFLICT_ERROR = 'delivery_policy_version_conflict'

SOURCES = ('delivery-policy-binding.json','delivery-policy-flow-report.json',
           'bcs-config.json','owned-bcs.json','local-fixtures.json',
           'local-delivery-policy.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-delivery-policy-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Delivery policy evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Delivery policy evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Delivery policy evidence exceeded byte limit')
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
    binding = read(root/'delivery-policy-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == PARENT
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Delivery policy frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'delivery-policy-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-delivery-policy.json') if (root/'local-delivery-policy.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Delivery policy final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Delivery policy runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_DELIVERY_POLICY_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_DELIVERY_POLICY_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else
              'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


def _business_evidence(root, binding, case):
    operations = case.get('operations', [])
    human_actor = case.get('human_actor')
    driver_bot = case.get('driver_bot_id')
    version = case.get('policy_version')
    original = case.get('original_policy')
    updated = case.get('updated_policy')
    change = case.get('policy_change')
    if (not isinstance(human_actor, str) or not human_actor.startswith('human_')
            or not isinstance(driver_bot, str) or not isinstance(version, int)
            or not isinstance(original, dict) or not isinstance(updated, dict)
            or not isinstance(change, dict)):
        return False
    if (change.get('field') != 'max_context_messages'
            or original.get('max_context_messages') != change.get('from')
            or updated.get('max_context_messages') != change.get('to')
            or change.get('from') == change.get('to')
            or {k: v for k, v in updated.items() if k != 'max_context_messages'}
               != {k: v for k, v in original.items() if k != 'max_context_messages'}):
        return False
    if ([r.get('operation') for r in operations] != list(V1_REQUIREMENTS)
            or case.get('binding_digest') != fingerprint(binding)
            or case.get('source_binding_digest') != fingerprint(binding['source_binding'])
            or case.get('conflict_error') != CONFLICT_ERROR
            or case.get('resources') != {'created': [], 'deleted': [], 'unknown': []}):
        return False
    for index, row in enumerate(operations):
        observed = row.get('observed', {})
        if (row.get('method') != V1_METHODS[index] or row.get('path') != PATH
                or row.get('http_status') != V1_STATUSES[index]
                or row.get('status') != 'VERIFIED'
                or row.get('transitions') != ['INTENDED', 'VERIFIED']
                or not isinstance(row.get('submitted_request_id'), str)
                or not row['submitted_request_id']):
            return False
        if index in (1, 2):
            expected_observed = {'http_status': V1_STATUSES[index]}
            expected_actor = 'bot:'+driver_bot
        elif index == 0:
            expected_observed = {'version': version, 'policy': original}
            expected_actor = human_actor
        elif index == 3:
            # Denied Bot writes precede the human CAS update, so the
            # effective policy is still the original at its original version.
            expected_observed = {'version': version, 'policy': original}
            expected_actor = human_actor
        elif index in (4, 8):
            expected_observed = {'version': version+(1 if index == 4 else 2),
                                 'updated_by': human_actor, 'policy': updated if index == 4 else original}
            expected_actor = human_actor
        elif index == 6:
            expected_observed = {'http_status': 409, 'error': CONFLICT_ERROR}
            expected_actor = human_actor
        else:
            expected_observed = {'version': version+(1 if index in (5, 7) else 2),
                                 'policy': updated if index in (5, 7) else original}
            expected_actor = human_actor
        if (observed != expected_observed or row.get('actor') != expected_actor):
            return False
        if index in (2, 4, 6, 8) and 'body_digest' not in row:
            return False
    ids = [row['submitted_request_id'] for row in operations]
    return len(set(ids)) == len(ids)


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
            'reports': {'expected_assertion_count': 10, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'ADMIN_POLICY_CAS_OWNED_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED',
                        'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                             'actual message delivery', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_delivery_policy(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Delivery policy source changed before report publication')
    _require(read(root/'delivery-policy-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Delivery policy publication must follow actual runtime finalization')
    pending = root/'delivery-policy-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in SOURCES}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'delivery-policy-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'delivery-policy-completion.json') == completion_digest,
             'Delivery policy source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_delivery_policy_report(root: Path):
    _require(not root.is_symlink(), 'Delivery policy report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'delivery-policy-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Delivery policy publication is incomplete')
        completion = read(root/'delivery-policy-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Delivery policy completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Delivery policy evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'delivery-policy-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Delivery policy evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Delivery policy completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
