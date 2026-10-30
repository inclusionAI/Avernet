"""Version-one Session owner reports projected from frozen owned-local evidence.

The owned-local in-process executor held the only credential-bearing traffic;
this offline projection only ever reads the frozen journal, the resource
ledger and the controlled BCS runtime record, and hashes each evidence file.
Digests detect drift, not a malicious rewrite by the owner of the Run files.
This slice is an independent migration candidate (its parent stays in the
known unmapped backlog), so the projection never claims release credit and
keeps the preprod public-v1 face honestly documented as NOT_RUN with its four
gap ids instead of pretending remote validation happened.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from ..inventory import binding_matches_reviewed_release, executor_digest
from ..profile import fingerprint
from ..run_state import RunStateError, read
from .session_owner_contract import (CASE_ID, CLEANUP_CHECKS, DOCUMENTED_GAPS,
                                       REQUIREMENTS, STATUS, STORY_CHECKS, SUITE)
from .local_session_owner_run import (BINDING_NAME, BINARY_KIND, CHECK_IDS, LEDGER_NAME,
                                       REQUIRED_ASSERTION_COUNT, RESOURCE_DISPOSITION_PASS,
                                       session_owner_source_binding)

SOURCES = ('session-owner-binding.json', 'session-owner-flow-report.json',
           'session-owner.json', 'session-owner-journal.json',
           'session-owner-resources.json', 'owned-bcs.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-session-owner-completion-v1'
EXPECTED_ASSERTIONS = 9
JOURNAL_NAME = 'session-owner-journal.json'
FLOW_NAME = 'session-owner-flow-report.json'
CASE_NAME = 'session-owner.json'
RESOURCES_NAME = 'session-owner-resources.json'
RUNTIME_NAME = 'owned-bcs.json'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Session owner evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8 * 1024 * 1024,
                 'Session owner evidence must be a bounded regular unshared file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'Session owner evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
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
    return {name for name in SOURCES if (root / name).is_symlink() or (root / name).exists()}


def _bound(root):
    binding = read(root / BINDING_NAME)
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and binding.get('run_id')
             and binding.get('runtime_dir') == str(root)
             and binding.get('binary_kind') == BINARY_KIND and binding.get('auto_build') is False
             and binding.get('executor_digest') == executor_digest()
             and binding.get('selected_requirement_ids') == list(REQUIREMENTS)
             and binding.get('bcs_binary_sha256') and len(binding['bcs_binary_sha256']) == 64
             and binding.get('environment') == {'local': 'LOCAL_EXECUTOR_ONLY',
                                                'preprod': 'REMOTE_UNSUPPORTED'}
             and binding.get('executor_admission', {}).get('allow_ambient_local_identity') is False
             and binding.get('executor_admission', {}).get('allow_mock_headers') is True
             and binding.get('source_binding', {}).get('parent') == 'story_session_owner_permissions'
             and binding.get('source_binding', {}).get('release_parent_denominator') is False
             and binding.get('source_binding', {}).get('independent_slice') is True
             and [row['id'] for row in binding['source_binding']['requirements']] == list(REQUIREMENTS)
             and binding.get('source_binding', {}).get('story_checks') == STORY_CHECKS
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Session owner frozen binding is invalid')
    return binding


def _journal(root):
    path = root / JOURNAL_NAME
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _case(root):
    path = root / CASE_NAME
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _resources(root):
    path = root / RESOURCES_NAME
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _runtime(root):
    path = root / RUNTIME_NAME
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _project(root, binding):
    flow = read(root / FLOW_NAME)
    journal = _journal(root)
    case = _case(root)
    resources = _resources(root)
    runtime = _runtime(root)
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('binary_kind') == BINARY_KIND
             and flow.get('bcs_binary_sha256') == binding['bcs_binary_sha256']
             and flow.get('auto_build') is False,
             'Session owner final result differs from its frozen declaration')
    _require(flow.get('journal') == (journal if journal else {}),
             'Session owner flow report and journal disagree')
    _require(flow.get('case') == case,
             'Session owner shared case receipts are not consistent across journals')
    story_lines = {row['id']: list(row['story_lines'])
                   for row in binding['source_binding']['requirements']}
    journal_complete = (bool(journal) and journal.get('status') == STATUS
                        and journal.get('case_id') == CASE_ID and journal.get('run_id') == binding['run_id']
                        and journal.get('observed_assertion_count') == STORY_CHECKS + CLEANUP_CHECKS
                        and journal.get('required_assertion_count') == REQUIRED_ASSERTION_COUNT
                        and journal.get('story_http_checks') == STORY_CHECKS
                        and journal.get('cleanup_http_checks') == CLEANUP_CHECKS
                        and journal.get('story_markers') == {'observed': True, 'http_checks': STORY_CHECKS}
                        and len(journal.get('operations', [])) == STORY_CHECKS + CLEANUP_CHECKS
                        and all(row.get('status') == 'ACKNOWLEDGED' for row in journal.get('operations', []))
                        and [row.get('id') for row in journal.get('requirements', [])] == list(REQUIREMENTS)
                        and all(row.get('status') == 'PASS' for row in journal.get('requirements', []))
                        and [row.get('id') for row in journal.get('checks', [])] == list(CHECK_IDS)
                        and all(row.get('status') == 'PASS' for row in journal.get('checks', []))
                        and journal.get('cleanup', {}).get('status') == 'PASS'
                        and journal.get('resource_disposition') == RESOURCE_DISPOSITION_PASS
                        and journal.get('remote_validation') == 'NOT_RUN'
                        and journal.get('grants_release_credit') is False
                        and journal.get('credential_persistence') == {
                            key: False for key in ('gateway_principal', 'register_token', 'bot_token',
                                                   'principal_signing_key', 'mock_admission_headers')}
                        and journal.get('execution_scope', {}).get('runtime') == 'OWNED_LOCAL_FOREGROUND'
                        and journal.get('execution_scope', {}).get('strict_mock_header_admission') is True
                        and journal.get('execution_scope', {}).get('ambient_local_identity') is False
                        and journal.get('execution_scope', {}).get('remote_capabilities') == 'NONE')
    resources_complete = (bool(resources)
                          and all(row.get('state') == 'deleted' for row in resources.get('resources', [])
                                  if row.get('kind') in ('bot', 'group', 'session'))
                          and len([row for row in resources.get('resources', [])
                                   if row.get('kind') == 'human']) == 3
                          and len([row for row in resources.get('resources', [])
                                   if row.get('kind') == 'bot']) == 2)
    runtime_complete = (runtime.get('status') == 'STOPPED' and runtime.get('returncode') == 0)
    case_complete = (bool(case) and case.get('status') == STATUS
                     and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                     and case.get('executor_digest') == binding['executor_digest']
                     and case.get('binary_kind') == BINARY_KIND
                     and case.get('bcs_binary_sha256') == binding['bcs_binary_sha256']
                     and [row['id'] for row in case.get('requirements', [])] == list(REQUIREMENTS)
                     and all(row.get('status') == 'PASS' for row in case.get('requirements', []))
                     and case.get('story_lines') == story_lines
                     and case.get('story_markers') == {'observed': True, 'http_checks': STORY_CHECKS}
                     and case.get('checks') == journal.get('checks', [])
                     and case.get('story_http_checks') == STORY_CHECKS
                     and case.get('cleanup_http_checks') == CLEANUP_CHECKS
                     and case.get('observed_assertion_count') == STORY_CHECKS + CLEANUP_CHECKS
                     and case.get('required_assertion_count') == REQUIRED_ASSERTION_COUNT
                     and case.get('resource_disposition') == RESOURCE_DISPOSITION_PASS
                     and case.get('remote_validation') == 'NOT_RUN')
    complete = (journal_complete and resources_complete and runtime_complete and case_complete
                and set(SOURCES) == _sources(root))
    stopped = runtime.get('returncode') == 0 if runtime else False
    cleared = journal_complete or (not journal and flow.get('status') == 'CANCELLED')
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == STATUS else
              'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'SESSION_GROUP_AND_OWNED_BOTS_DELETED' if resources_complete else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next(
                (row['status'] for row in case.get('requirements', []) if row.get('id') == name), 'NOT_RUN')}
                for name in REQUIREMENTS]}


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    case_requirements = projected.get('requirements', [])
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID],
                      'legacy_inventory_count': len(binding['legacy_inventory']),
                      'evidence_granularity': 'OWNED_SESSION_OWNER_JOURNAL',
                      'independent_slice': True, 'release_parent_denominator': False,
                      'environment': binding['environment'],
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'manifest_digest': binding['executor_digest']},
            'runner': {'executor_digest': binding['executor_digest'],
                       'binary_kind': BINARY_KIND, 'auto_build': False,
                       'required_arguments': ['--binary', '--binary-sha256']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': case_requirements or [{'id': name, 'status': 'NOT_RUN'} for name in REQUIREMENTS],
            'reports': {'expected_assertion_count': EXPECTED_ASSERTIONS,
                        'observed_assertion_count': len(case_requirements)},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
            'documented_gaps': DOCUMENTED_GAPS,
            'routes': [],
            'cleanup': {'verification_scope': 'OWNED_SESSION_GROUP_AND_BOT_DISPOSITION',
                        'physical_data_erasure': 'OWNED_RUNTIME_DIRECTORY_DISPOSABLE',
                        'associated_history_purge': 'NOT_PERFORMED'},
            'verification_boundary': {'not_proven': [
                'preprod public-v1 execution: four documented gaps '
                '(PREPROD_STATION_BOOTSTRAP_FACE_ABSENT, PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE, '
                'PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY, PREPROD_PARTICIPANT_ROUTES_UNBOUND)',
                'three distinct preprod Human principals plus per-Human Bot registration',
                'remote authentication and authorization', 'Bot engine reasoning',
                'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
            'ready_to_execute': False}


def publish_session_owner_report(root: Path, binding: dict, result: dict):
    root = Path(root).resolve()
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Session owner source changed before report publication')
    _require(read(root / FLOW_NAME) == result,
             'Session owner publication must follow the actual run result')
    pending = root / 'session-owner-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root / name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from ..reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION,
                  'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root / 'session-owner-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root / name) == digest for name, digest in hashes.items())
             and all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'session-owner-completion.json') == completion_digest,
             'Session owner source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_session_owner_report(root: Path):
    _require(not root.is_symlink(), 'Session owner report requires original non-symlink root')
    root = Path(root).resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root / 'session-owner-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Session owner publication is incomplete')
        completion = read(root / 'session-owner-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Session owner completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS),
                 'Session owner completion does not match the frozen evidence set')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items())
                 and read(root / 'session-owner-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Session owner evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Session owner completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
