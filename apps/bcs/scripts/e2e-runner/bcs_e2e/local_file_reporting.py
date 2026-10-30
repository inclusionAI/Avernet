"""Bound offline reports for the actual owned-local CLI File parent Story.

A pending marker precedes publication and is removed only after all source and
completion checks. Original journals are never repaired to manufacture a PASS.
No network, credential resolution or remote closure inference occurs here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .errors import has_cancellation
from .inventory import binding_matches_reviewed_release, ROOT, current_manifest, executor_digest
from .migration_inventory import validate_contract
from .profile import fingerprint
from .run_state import RunStateError, read, write
from .ledger import Ledger
from .local_file_setup import REQUIRED as SETUP_ASSERTIONS
from .local_file_story import CASE_ID, SUITE, LEAVES, REQUIREMENTS

LEGACY_CONTRACT = 'owned-local-cli-file-binding-v1'
CONTRACT = 'owned-local-cli-file-binding-v2'
COMPLETION = 'owned-local-cli-file-completion-v1'
SOURCES = ('file-story-binding.json', 'bcs-config.json', 'owned-bcs.json',
           'local-fixtures.json', 'cli-file-setup.json', 'local-file-story.json', 'cli-file-resources.sqlite')


def _require(condition, message):
    if not condition:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'CLI-file report source must be bounded regular unshared data')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'CLI-file source exceeds byte budget')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def freeze_binding(runtime, cli_digest: str) -> dict:
    contract = validate_contract(read(ROOT/'migration-contracts.json'), source_root=ROOT.parent/'e2e-test')
    parent = next(s for s in contract['stories'] if s['legacy_id'] == 'story_session_file_workspace')
    _require([r['id'] for r in parent['requirements']] == ['CLI-FILE.'+r for r in REQUIREMENTS],
             'CLI-file parent obligations changed')
    value = {'schema_version': 1, 'contract': CONTRACT, 'suite': SUITE, 'case_id': CASE_ID, 'case_version': 1,
        'run_id': runtime.run_id, 'runtime_dir': str(runtime.root), 'origin': runtime.inputs.origin,
        'executor_digest': executor_digest(), 'bcs_binary_sha256': runtime.binary_sha256,
        'cli_binary_sha256': cli_digest, 'migration_contract_digest': fingerprint(contract),
        'required_assertions': ['CLI-FILE.'+r for r in REQUIREMENTS], 'cli_leaves': list(LEAVES),
        'legacy_inventory': current_manifest()['legacy_inventory'], 'ready_to_execute': False,
        'full_release_status': 'NOT_QUALIFIED'}
    _exclusive(runtime.root/'file-story-binding.json', value)
    return value


def _bound(root: Path):
    _require(root.is_absolute() and not root.is_symlink() and root.is_dir(), 'CLI-file report requires its exact owned directory')
    binding = read(root/'file-story-binding.json')
    _require(binding.get('contract') in (LEGACY_CONTRACT, CONTRACT) and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and type(binding.get('case_version')) is int
             and binding['case_version'] == 1 and binding.get('runtime_dir') == str(root)
             and binding.get('required_assertions') == ['CLI-FILE.'+r for r in REQUIREMENTS]
             and binding.get('cli_leaves') == list(LEAVES) and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'CLI-file frozen source inventory is invalid')
    return binding


def _sources(root):
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _retained_visibility(setup: dict) -> dict:
    result = {'scope': 'OWNED_LOCAL_FIXTURE', 'role': 'ENG',
              'setup_visibility': 'NOT_VERIFIED', 'restoration': 'NOT_VERIFIED',
              'current_state_verification': 'NOT_PERFORMED'}
    if not setup:
        result['evidence_status'] = 'SETUP_EVIDENCE_MISSING'
        return result
    if setup.get('status') != 'LOCAL_CLI_FILE_SETUP_PASS':
        result['evidence_status'] = 'FAILED_SETUP_NOT_VERIFIED'
        return result
    admission = setup.get('participant_admission')
    assertion_id = CASE_ID + '.participant-public'
    assertions = setup.get('assertions', [])
    if (isinstance(admission, dict)
            and admission.get('operation') == 'participant-visibility-public'
            and admission.get('status') == 'ACKNOWLEDGED'
            and admission.get('resource_binding') == 'BOUND_OWNED_ENG'
            and any(isinstance(item, dict) and item.get('id') == assertion_id
                    and item.get('status') == 'PASS' for item in assertions)):
        result.update(evidence_status='SETUP_ACKNOWLEDGED', setup_visibility='public',
                      restoration='NOT_PERFORMED')
    else:
        result['evidence_status'] = 'SUCCESS_SETUP_EVIDENCE_INCOMPLETE'
    return result


def _project(root: Path, binding: dict, *, failed=False, cancelled=False):
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-file-story.json') if (root/'local-file-story.json').exists() else {}
    setup = read(root/'cli-file-setup.json') if (root/'cli-file-setup.json').exists() else {}
    resources = []
    if setup and (root/'cli-file-resources.sqlite').is_file():
        with Ledger(root/'cli-file-resources.sqlite', binding['run_id'], setup['binding_digest'], read_only=True) as ledger:
            resources = ledger.resource_snapshot()
    assertions = case.get('assertions', [])
    _require(isinstance(assertions, list), 'CLI-file assertions are invalid')
    if case:
        _require(case.get('binding_digest') == fingerprint(binding) and case.get('case_id') == CASE_ID
                 and case.get('run_id') == binding['run_id'] and case.get('setup') == setup,
                 'CLI-file journal is not bound to its Run and exact setup')
    if setup:
        _require(setup.get('case_id') == CASE_ID and setup.get('run_id') == binding['run_id']
                 and setup.get('executor_digest') == binding['executor_digest'], 'CLI-file setup is not bound')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'], 'CLI-file shutdown binding differs')
    ids = [a.get('id') for a in assertions if isinstance(a, dict)]
    requirements = [{'id': 'CLI-FILE.'+name, 'status': next((a['status'] for a in assertions if a.get('id') == 'CLI-FILE.'+name), 'NOT_RUN')}
                    for name in REQUIREMENTS]
    operations = case.get('operations', [])
    expected_setup = {CASE_ID+'.'+name for name in SETUP_ASSERTIONS}
    setup_assertions = setup.get('assertions', [])
    retained_visibility = _retained_visibility(setup)
    extended = binding.get('contract') == CONTRACT
    deleted_observations = [item for item in case.get('http_observations', [])
                            if isinstance(item, dict) and item.get('operation') in
                            ('deleted-metadata', 'shared-deleted-metadata', 'shared-deleted-content')]
    deletion_verified = [(item.get('operation'), item.get('http_status'), item.get('anonymous'),
                          item.get('resource_id')) for item in deleted_observations] == [
        ('deleted-metadata', 404, False, case.get('file_id')),
        ('shared-deleted-metadata', 404, True, case.get('file_id')),
        ('shared-deleted-content', 404, True, case.get('file_id'))]
    file_rows = [r for r in resources if r['kind']=='file']
    resources_complete = (len(resources)==4 and len(file_rows)==1
        and file_rows[0]['resource_id']==case.get('file_id') and file_rows[0]['handle']==case.get('file_handle')
        and file_rows[0]['state']=='deleted' and file_rows[0]['origin']=='created'
        and file_rows[0]['parent']==setup.get('resources', {}).get('session', {}).get('handle')
        and all(r['case_id']==CASE_ID and r['attempt']==1 and r['origin']=='created' for r in resources))
    wire_complete = all(isinstance(op.get('wire'), list) and len(op['wire'])==(3 if op.get('operation')=='upload' else 1)
        and all(r.get('dispatch_status')=='RESPONSE_RECEIVED' and r.get('http_status') in (200,201,202,204) for r in op['wire'])
        for op in operations)
    complete = (case.get('status') == 'PASS' and ids == binding['required_assertions']
        and all(a.get('status') == 'PASS' for a in assertions)
        and [op.get('operation') for op in operations] == list(LEAVES)
        and all(op.get('status') == 'ACKNOWLEDGED' and op.get('credential_cleanup') == 'CLEARED'
                and op.get('child_shutdown_status') != 'UNCONFIRMED' for op in operations)
        and setup.get('status') == 'LOCAL_CLI_FILE_SETUP_PASS'
        and len(setup_assertions)==len(expected_setup) and {a['id'] for a in setup_assertions}==expected_setup
        and all(a['status']=='PASS' for a in setup_assertions) and resources_complete and wire_complete
        and [op.get('operation') for op in setup.get('operations', [])] == ['group-create', 'session-create']
        and all(op.get('status')=='ACKNOWLEDGED' and op.get('credential_cleanup')=='CLEARED'
                for op in setup.get('operations', []))
        and (not extended or (retained_visibility['evidence_status'] == 'SETUP_ACKNOWLEDGED'
                              and deletion_verified))
        and case.get('file_delete') == 'PASS')
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    status = ('CANCELLED' if cancelled or case.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and not failed else 'FAIL' if case.get('status') == 'FAIL' else 'BLOCKED')
    if case.get('status') == 'PASS' and not complete:
        status = 'BLOCKED'
    return {'schema_version': 1, 'case_id': CASE_ID, 'version': 1, 'run_id': binding['run_id'],
        'status': status, 'functional': 'PASS' if complete else case.get('status', 'NOT_RUN'),
        'evidence_status': 'PASS' if complete else 'INCOMPLETE',
        'cleanup': 'PASS' if complete and stopped else 'BLOCKED', 'assertions': assertions,
        'requirements': requirements, 'operations': operations,
        'http_observations': case.get('http_observations', []), 'runtime_shutdown': runtime,
        'cli_child_shutdown': 'UNCONFIRMED' if any(op.get('child_shutdown_status')=='UNCONFIRMED' for op in operations) else 'NO_UNCONFIRMED_CHILD_RECORDED',
        **({'retained_visibility': retained_visibility} if extended else {}),
        'resource_disposition': 'FILE_DELETED_GROUP_SESSIONS_HISTORY_RETAINED' if complete else 'RETAINED_LOCAL_DATABASE',
        'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False}


def _summary(binding: dict, case: dict, issues: list):
    status = case.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
        'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
        'target': {'tier': 'local', 'environment_id': binding['run_id']},
        'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(current_manifest()['legacy_inventory']),
                  'full_release_status': 'NOT_QUALIFIED', 'manifest_digest': fingerprint(binding),
                  'file_observation_contract': binding.get('contract')},
        'runner': {'executor_digest': binding['executor_digest']},
        'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                   'functional': case.get('functional', 'NOT_RUN'), 'evidence_status': case.get('evidence_status','INCOMPLETE'),
                   'cleanup': case.get('cleanup', 'BLOCKED'), 'resource_disposition': case.get('resource_disposition','UNVERIFIED'),
                   'retained_visibility': case.get('retained_visibility', _retained_visibility({}))}],
        'requirements': case.get('requirements', []),
        'reports': {'expected_assertion_count': len(REQUIREMENTS), 'observed_assertion_count': len(case.get('assertions', []))},
        'integrity': {'status': 'PASS' if not issues else 'BLOCKED', 'issues': issues},
        'routes': [r for op in case.get('operations', []) for r in op.get('wire', [])],
        'cleanup': {'verification_scope': 'OWNED_FILE_AND_RUNTIME_ONLY', 'physical_data_erasure': 'NOT_VERIFIED',
                    'associated_history_purge': 'NOT_PERFORMED',
                    'retained_visibility': case.get('retained_visibility', _retained_visibility({}))},
        'mock_boundary': {'not_proven': ['remote gateway/BaaS', 'Bot engine execution', 'notification delivery', 'full release coverage']},
        'timing': {'interpretation': 'functional diagnostic timing only; not a performance benchmark'},
        'ready_to_execute': False}


def build_file_report(root: Path):
    _require(not root.is_symlink(), 'CLI-file root symlink is not admitted')
    root = root.resolve()
    binding = _bound(root)
    issues = []
    case = {}
    try:
        _require(not (root/'file-publication-pending.json').exists() and not (root/'file-publication-pending.json').is_symlink(), 'CLI-file publication is incomplete')
        completion = read(root/'file-completion.json')
        _require(type(completion.get('failed')) is bool and type(completion.get('cancelled')) is bool,
                 'CLI-file completion outcomes are invalid')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'CLI-file completion binding is invalid')
        expected = _sources(root) | {'report-'+CASE_ID+'.json'}
        _require(set(completion['sources']) == expected and all(_sha(root/n) == h for n,h in completion['sources'].items()),
                 'CLI-file original source or report changed')
        case = read(root/('report-'+CASE_ID+'.json'))
        projected = _project(root, binding, failed=completion['failed'], cancelled=completion['cancelled'])
        _require(case == projected, 'CLI-file Case does not match source evidence')
        _require(all(_sha(root/n)==h for n,h in completion['sources'].items())
                 and read(root/'file-completion.json')==completion
                 and not (root/'file-publication-pending.json').exists(),
                 'CLI-file sources changed during offline report')
    except (RunStateError, OSError, KeyError, TypeError, ValueError):
        issues.append('CLI-file completion or source evidence is missing, invalid or changed')
    return _summary(binding, case, issues)


def publish(runtime, binding: dict, failure):
    root = runtime.root
    _require(executor_digest() == binding['executor_digest'] and _bound(root) == binding,
             'CLI-file sources changed before publication')
    _require(read(root/'owned-bcs.json') == runtime._record(), 'CLI-file runtime receipt differs from live child outcome')
    pending = root/'file-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version':1, 'run_id':binding['run_id']})
    source_hashes = {n:_sha(root/n) for n in _sources(root)}
    cancelled = failure is not None and has_cancellation(failure)
    case = _project(root, binding, failed=failure is not None, cancelled=cancelled)
    report_name = 'report-'+CASE_ID+'.json'
    source_hashes[report_name] = _exclusive(root/report_name, case)
    from .reporting import write_reports
    summary = _summary(binding, case, [])
    outputs = write_reports(summary, root/'reports', {'all'})
    completion = {'schema_version':1, 'contract': COMPLETION, 'binding_digest':fingerprint(binding),
                  'sources':source_hashes, 'failed':failure is not None, 'cancelled':cancelled}
    completion_digest = _exclusive(root/'file-completion.json', completion)
    _require(_sources(root) | {report_name} == set(source_hashes)
             and all(_sha(root/n) == h for n,h in source_hashes.items()), 'CLI-file source drift during publication')
    _require(_sha(pending)==pending_digest and _sha(root/'file-completion.json')==completion_digest,
             'CLI-file publication markers changed')
    pending.unlink()
    # No further required writes after this publication commit point.
    return {'schema_version':1, 'status':summary['status'], 'run_id':binding['run_id'],
            'summary':summary, 'outputs':outputs, 'ready_to_execute':False}
