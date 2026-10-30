"""Version-one Agent-network reports projected from original frozen evidence.

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

CASE_ID = 'LOCAL-NETWORK-01'
SUITE = 'owned-local-network-v1'
V1_REQUIREMENTS = tuple('NETWORK.'+name for name in (
    'platform', 'actor-list', 'actor-search', 'actor-status', 'bots-my',
    'bots-paged', 'bots-query', 'register-onboard'))
SOURCES = ('network-binding.json', 'network-flow-report.json', 'bcs-config.json',
           'owned-bcs.json', 'local-fixtures.json', 'panel-asset.json',
           'panel/index.umd.js', 'local-network-api.json', 'local-network-ledger.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-network-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Agent-network evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Agent-network evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Agent-network evidence exceeded byte limit')
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
    binding = read(root/'network-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_user_prepares_agent_network'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Agent-network frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'network-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-network-api.json') if (root/'local-network-api.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Agent-network final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Agent-network runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_NETWORK_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_NETWORK_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


def _business_evidence(root, binding, case):
    ledger = read(root/'local-network-ledger.json')
    fixtures = read(root/'local-fixtures.json')
    roles = ('CEO', 'PM', 'ENG', 'QA')
    operations = case.get('operations', [])
    names = ([f'initial-fixture-{role.lower()}' for role in roles] + ['initial-bots-my-snapshot']
             + ['platform-health', 'platform-me', 'platform-repair-info', 'platform-missing-secret',
                'actor-list', 'actor-search', 'actor-status-before', 'actor-status-hidden',
                'actor-status-hidden-readback', 'actor-status-online', 'actor-status-online-readback',
                'actor-status-final', 'bots-my', 'bots-paged', 'bots-query', 'register-token',
                'register-owned-bot', 'registered-owner-proof', 'onboard-owner-proof',
                'onboard-owned-bot', 'browser-url-owner-proof', 'browser-onboard-url',
                'admin-owner-proof', 'admin-reonboard-owned-bot', 'admin-detail-readback',
                'delete-owner-proof', 'delete-owned-bot', 'deleted-bot-404']
             + [f'final-fixture-{role.lower()}' for role in roles] + ['final-bots-my-snapshot'])
    if [op.get('name') for op in operations] != names:
        return False
    for op in operations:
        expected_status = 404 if op['name'] in ('platform-missing-secret', 'deleted-bot-404') else 200
        transitions = op.get('transitions', [])
        if (op.get('status') != 'ACKNOWLEDGED' or op.get('http_status') != expected_status
                or [row.get('status') for row in transitions] != ['INTENDED', 'ACKNOWLEDGED']
                or transitions[-1].get('http_status') != expected_status
                or op.get('requirement_id') not in V1_REQUIREMENTS
                or not isinstance(op.get('submitted_request_id'), str)
                or not op['submitted_request_id'].startswith('e2e-network-')):
            return False
    if len({op['submitted_request_id'] for op in operations}) != len(operations):
        return False
    resource = ledger.get('resource', {})
    by_name = {op['name']: op for op in operations}
    states = ['NONE', 'INTENDED', 'LIVE', 'ONBOARDED', 'ADMIN_REVIEWED', 'DELETED_CONFIRMED_404']
    before, after = case.get('non_target_before', {}), case.get('non_target_after', {})
    bot_id = resource.get('resource_id')
    if not isinstance(bot_id, str) or not bot_id or set(before) != set(roles) or before != after:
        return False
    bound_ids = {fixtures['roles'][role]['bot_id'] for role in roles}
    if (bot_id in bound_ids or any(before[role].get('bot_uuid') != fixtures['roles'][role]['bot_id'] for role in roles)
            or before['CEO'].get('status') != 'online'
            or case.get('owned_bot_inventory_before') != case.get('owned_bot_inventory_after')
            or not bound_ids <= set(case.get('owned_bot_inventory_before', []))):
        return False
    panel = case.get('platform', {}).get('panel', {})
    return (ledger.get('contract') == 'owned-local-network-ledger-v1'
            and ledger.get('run_id') == binding['run_id'] and ledger.get('case_id') == CASE_ID
            and ledger.get('executor_digest') == binding['executor_digest']
            and ledger.get('binding_digest') == fingerprint(binding)
            and case.get('binding_digest') == fingerprint(binding)
            and case.get('fixture_digest') == fingerprint(fixtures)
            and ledger.get('status') == 'RELEASED_EXACT_RESOURCE'
            and [row.get('resource_state') for row in ledger.get('transitions', [])] == states
            and resource.get('kind') == 'bot' and resource.get('state') == states[-1]
            and resource.get('owner_id') == case['platform']['human']['user_id']
            and resource.get('request_id') == by_name['register-owned-bot']['submitted_request_id']
            and resource.get('token_persisted') is False
            and by_name['delete-owned-bot']['path'] == '/bots/'+bot_id
            and by_name['deleted-bot-404']['path'] == '/bots/'+bot_id
            and by_name['actor-status-hidden']['path'] == '/actors/'+before['CEO']['bot_uuid']+'/status'
            and by_name['actor-status-online']['path'] == by_name['actor-status-hidden']['path']
            and case.get('credentials') == {'new_bot_memory': 'CLEARED_REFERENCE_ONLY'}
            and case.get('fixture_preservation', {}).get('status') == 'PASS'
            and case.get('resource_disposition') == 'TEMPORARY_BOT_DELETED_FIXTURES_RETAINED'
            and panel.get('status') == 'PASS' and panel.get('sha256') == binding['panel_sha256']
            and _sha(root/'panel/index.umd.js') == binding['panel_sha256'])


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED', 'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': projected.get('requirements', [{'id': name, 'status': 'NOT_RUN'} for name in V1_REQUIREMENTS]),
            'reports': {'expected_assertion_count': 8, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'OWNED_TEMPORARY_BOT_AND_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'remote onboarding and cleanup', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_network(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Agent-network source changed before report publication')
    _require(read(root/'network-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Agent-network publication must follow actual runtime finalization')
    pending = root/'network-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'network-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'network-completion.json') == completion_digest,
             'Agent-network source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_network_report(root: Path):
    _require(not root.is_symlink(), 'Agent-network report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'network-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Agent-network publication is incomplete')
        completion = read(root/'network-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Agent-network completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Agent-network evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'network-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Agent-network evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Agent-network completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
