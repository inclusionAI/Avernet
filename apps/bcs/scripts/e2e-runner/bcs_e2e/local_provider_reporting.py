"""Version-one Provider/Organization reports projected from original frozen evidence.

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

CASE_ID = 'LOCAL-PROVIDER-01'
SUITE = 'owned-local-provider-v1'
from .local_provider_contract import REQUIREMENTS as V1_REQUIREMENTS

SOURCES = ('provider-binding.json', 'provider-flow-report.json', 'bcs-config.json',
           'owned-bcs.json', 'local-fixtures.json', 'provider-sink-final.json',
           'local-provider-api.json', 'local-provider-ledger.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-provider-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Provider/Organization evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Provider/Organization evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Provider/Organization evidence exceeded byte limit')
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
    binding = read(root/'provider-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_provider_operator_publishes_agent'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Provider/Organization frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'provider-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-provider-api.json') if (root/'local-provider-api.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Provider/Organization final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Provider/Organization runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_PROVIDER_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    downstream = read(root/'provider-sink-final.json')['sink']
    closed = (flow.get('downstream') == downstream and downstream.get('state') == 'CLOSED'
              and downstream.get('failed') is False and downstream.get('received') == 1
              and downstream.get('rejected') == 1 and downstream.get('engine') == 'NOT_STARTED')
    cleared = (flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
               and case.get('credentials', {}).get('credential_memory') == 'CLEARED')
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and closed and flow.get('status') == 'LOCAL_PROVIDER_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'RETAINED' if complete and stopped and cleared and closed else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


def _business_evidence(root, binding, case):
    from urllib.parse import quote
    ledger = read(root/'local-provider-ledger.json')
    fixtures = read(root/'local-fixtures.json')
    resources = ledger.get('resources', {})
    parents = {'provider': None, 'bot': 'provider', 'organization': 'provider',
               'member': 'organization', 'admin_run': 'organization'}
    if (set(resources) != set(parents) or case.get('resources') != resources
            or case.get('ledger') != ledger or case.get('ledger_digest') != fingerprint(ledger)
            or case.get('binding_digest') != fingerprint(binding)
            or case.get('source_binding_digest') != fingerprint(binding['source_binding'])
            or any(ledger.get(k) != binding[k] for k in ('run_id', 'case_id', 'suite'))
            or ledger.get('binding_digest') != fingerprint(binding)):
        return False
    operations = case.get('operations', [])
    if [r.get('operation') for r in operations] != list(V1_REQUIREMENTS):
        return False
    ids = {key: row.get('resource_id') for key, row in resources.items()}
    if (any(not isinstance(value, str) or not value for value in ids.values())
            or ids['member'] != ids['bot'] or len({ids[k] for k in ('provider','bot','organization','admin_run')}) != 4):
        return False
    substitutions = {'provider_id': ids['provider'], 'provider_bot_uuid': ids['bot'],
                     'organization_code': ids['organization'], 'provider_bot_ref': binding['run_id'],
                     'admin_run_id': ids['admin_run']}
    request_ids = []
    for operation, requirement in zip(operations, binding['source_binding']['requirements']):
        suffix = operation['operation'].removeprefix('PROVIDER.')
        path = requirement['path']
        for key, value in substitutions.items():
            path = path.replace('${'+key+'}', quote(value, safe=''))
        status = 409 if suffix == 'duplicate-slug-conflict' else 403 if suffix.endswith('-denied') else 404 if suffix == 'unknown-run-callback' else 204 if suffix == 'member-remove' else 200
        auth = 'human' if suffix in ('register','duplicate-slug-conflict') else 'bot_runtime' if suffix in ('agentpass-negative','unknown-run-callback') else 'provider_admin'
        if (operation.get('status') != 'VERIFIED' or operation.get('http_status') != status
                or operation.get('method') != requirement['method'] or operation.get('path') != path
                or operation.get('auth') != auth
                or operation.get('provider_header') is not (suffix in ('admin-start','admin-read','agentpass-negative','unknown-run-callback'))
                or operation.get('transitions') != ['INTENDED','ACKNOWLEDGED','VERIFIED']
                or not isinstance(operation.get('submitted_request_id'), str) or not operation['submitted_request_id']):
            return False
        request_ids.append(operation['submitted_request_id'])
    if len(set(request_ids)) != 32:
        return False
    creates = {'provider': 0, 'bot': 4, 'organization': 8, 'member': 14, 'admin_run': 20}
    deletes = {'member': (22, 'organization-member-remove'), 'bot': (30, 'bot-retired')}
    for key, parent in parents.items():
        row = resources[key]
        if (row.get('handle') != key or row.get('kind') != key or row.get('parent') != parent
                or row.get('case_id') != CASE_ID or row.get('attempt') != 1 or row.get('origin') != 'created'
                or row.get('request_id') != request_ids[creates[key]]):
            return False
        history = [{'state': s, 'request_id': row['request_id']} for s in ('INTENDED','LIVE')]
        if key in deletes:
            index, check = deletes[key]
            history += [{'state': s, 'request_id': request_ids[index]} for s in ('DELETE_INTENDED','DELETE_ACKNOWLEDGED')]
            history.append({'state': 'DELETED', 'request_id': request_ids[index], 'check': check})
        else:
            history.append({'state': 'RETAINED', 'request_id': row['request_id']})
        if row.get('history') != history or row.get('state') != history[-1]['state']:
            return False
    p, b, org, run = ids['provider'], ids['bot'], ids['organization'], ids['admin_run']
    expected = {
        'register': {'provider_id': p, 'credential_kinds_returned': ['provider_admin','downlink']},
        'duplicate-slug-conflict': {'conflict': True},
        'read': {'provider_id': p, 'disabled': False, 'auth_mode': 'static_bearer'},
        'update': {'provider_id': p, 'disabled': False, 'name': 'E2E Provider Updated'},
        'bot-publish': {'provider_id': p, 'provider_bot_ref': binding['run_id'], 'bot_uuid': b},
        'bot-update': {'bot_uuid': b, 'provider_bot_ref': binding['run_id'], 'name': 'Provider Review Agent (Senior)', 'domains': ['release','audit'], 'scopes': []},
        'attributes-read-denied': {'denied': True}, 'attributes-write-denied': {'denied': True},
        'org-create': {'organization_code': org, 'name': 'E2E release organization'},
        'org-list': {'organizations': [{'organization_code': org}]},
        'org-read': {'organization_code': org, 'name': 'E2E release organization'},
        'org-update': {'organization_code': org, 'name': 'E2E release organization updated'},
        'candidate-list-before': {'bots': [{'bot_uuid': b}]}, 'candidate-before': {'bot_uuid': b, 'is_member': False},
        'member-add': {'bot_uuid': b, 'role': 'reviewer'}, 'candidate-list-after': {'bots': []},
        'candidate-after': {'bot_uuid': b, 'is_member': True}, 'member-profile': {'profile': {'name': 'E2E organization bot'}},
        'member-read': {'bot_uuid': b, 'role': 'reviewer'}, 'member-list': {'members': [{'bot_uuid': b}]},
        'member-remove': {'removed_bot_uuid': b, 'organization_code': org, 'deletion': 'LOGICAL'},
        'bot-list': {'items': [{'bot_uuid': b}]},
        'roster-denied': {'denied': True, 'diagnostic': 'not allowed to access the task-mode roster'},
        'agentpass-negative': {'agent_code': None, 'provider_bot_binding': None},
        'unknown-run-callback': {'error': 'run_not_found'},
        'takeover-denied': {'denied': True, 'diagnostic': 'not allowed to switch bot delivery'},
        'disable': {'provider_id': p, 'disabled': True}, 'enable': {'provider_id': p, 'disabled': False},
        'bot-retire': {'deleted': True, 'provider_id': p, 'provider_bot_ref': binding['run_id'], 'bot_uuid': b},
        'bot-list-empty': {'items': []}}
    downstream = case.get('downstream', {})
    final = read(root/'provider-sink-final.json')['sink']
    if (dict(downstream, state='CLOSED') != final or downstream.get('state') != 'LIVE'
            or downstream.get('failed') is not False or downstream.get('received') != 1
            or downstream.get('rejected') != 1 or downstream.get('engine') != 'NOT_STARTED'
            or len(downstream.get('records', [])) != 1):
        return False
    record = downstream['records'][0]
    if (record.get('provider_run_id') != run or record.get('provider_id') != p or record.get('bot_id') != b
            or record.get('owner_run_id') != binding['run_id'] or record.get('http_status') != 503
            or record.get('status') != 'REJECTED_NO_ENGINE' or record.get('engine') != 'NOT_STARTED'):
        return False
    admin = {'data': {'run_id': run, 'provider_id': p, 'organization_code': org, 'target_bot_uuid': b,
                      'session_id': record['session_id'], 'status': 'delivery_failed'},
             'variant': 'REJECTED_NO_ENGINE', 'engine_success': False}
    expected.update({'admin-start': admin, 'admin-read': admin})
    if any(op.get('observed') != expected[op['operation'].removeprefix('PROVIDER.')] for op in operations):
        return False
    checks = case.get('checks', [])
    names = ('human-before','fixtures-before','fresh-provider-namespace','provider-register','provider-update',
             'bot-created','bot-updated','attributes-unchanged','organization-org-create','organization-org-update',
             'organization-member-add','organization-member-remove','provider-disable','provider-enable','bot-retired',
             'fixtures-after','provider-retained','organization-retained')
    if [c.get('id') for c in checks] != list(names) or any(c.get('status') != 'OBSERVED' for c in checks):
        return False
    evidence = {c['id']: c['evidence'] for c in checks}
    before, after = case.get('fixture_state', {}).get('before'), case.get('fixture_state', {}).get('after')
    if (not before or before != after or before != evidence['fixtures-before'] or after != evidence['fixtures-after']
            or case.get('preserved') != {'roles': ['CEO','PM','ENG','QA'], 'unchanged': True, 'qa_takeover': 'DENIED_UNCHANGED'}
            or evidence['bot-updated'] != evidence['attributes-unchanged']):
        return False
    human = fixtures['human']['user_id']
    provider = evidence['provider-retained']['provider']
    retired = evidence['bot-retired']
    organization = evidence['organization-retained']
    if (provider.get('provider_id') != p or provider.get('created_by') != human or provider.get('owners') != [human]
            or provider.get('disabled') != 0 or provider.get('sink_url_sha256') != binding['sink_url_sha256']
            or provider.get('admin_callback_configured') is not False
            or retired['bot'].get('is_deleted') != 1 or retired['bot'].get('bot_uuid') != b
            or retired['binding'].get('disabled') != 1 or retired['binding'].get('provider_id') != p
            or organization['organization'].get('code') != org or organization['organization'].get('managing_provider_id') != p
            or organization.get('members') != [{'organization_code': org, 'bot_uuid': b, 'role': 'reviewer', 'disabled': 1}]):
        return False
    return (case.get('credentials', {}).get('credential_memory') == 'CLEARED'
            and case.get('resource_disposition') == 'RETAINED_WITH_ORIGINAL_BUSINESS_DELETIONS'
            and case.get('retention') == {'provider': 'RETAINED_ENABLED', 'provider_credentials': 'RETAINED_IN_PRODUCT',
                'organization': 'RETAINED', 'admin_run': 'RETAINED', 'member': 'LOGICALLY_REMOVED_HISTORY_RETAINED',
                'bot': 'SOFT_DELETED_BINDING_DISABLED', 'database': 'RETAINED', 'cleanup': 'NOT_RUN'})


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
            'reports': {'expected_assertion_count': 32, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'OWNED_BOT_MEMBERSHIP_AND_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED',
                        'retained_resources': ['provider', 'organization', 'provider_credentials'],
                        'process_local_admin_run': 'PROCESS_STOPPED_NOT_ENGINE_SUCCESS' if projected.get('cleanup') == 'RETAINED' else 'UNVERIFIED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'successful provider engine execution', 'remote Provider lifecycle and cleanup', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_provider(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Provider/Organization source changed before report publication')
    _require(read(root/'provider-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Provider/Organization publication must follow actual runtime finalization')
    pending = root/'provider-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'provider-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'provider-completion.json') == completion_digest,
             'Provider/Organization source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_provider_report(root: Path):
    _require(not root.is_symlink(), 'Provider/Organization report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'provider-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Provider/Organization publication is incomplete')
        completion = read(root/'provider-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Provider/Organization completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Provider/Organization evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'provider-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Provider/Organization evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Provider/Organization completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
