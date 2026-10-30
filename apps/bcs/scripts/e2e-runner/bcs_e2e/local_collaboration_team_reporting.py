"""Frozen reporting for the owned-local collaboration-team parent Story."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .collaboration_team_profile import validate_collaboration_team_profile
from .collaboration_team_contract import (
    ADAPTER_METHODS,
    CASE_ID,
    REQUIREMENT_IDS,
    SUITE,
    collaboration_team_source_binding,
)
from .inventory import binding_matches_reviewed_release, executor_digest
from .local_operator_cli_contract import COLLABORATION_TEAM_PARENT
from .profile import fingerprint
from .run_state import RunStateError, read

SOURCES = ('collaboration-team-binding.json', 'collaboration-team-flow-report.json',
           'bcs-config.json', 'owned-bcs.json', 'local-fixtures.json', 'collaboration-team-profile.json',
           'local-collaboration-team.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-collaboration-team-completion-v1'
RESOURCE_DISPOSITION = 'OWNED_RUNTIME_BUSINESS_HISTORY_RETAINED'
_STEP_KEYS = {'requirement_id', 'method', 'status', 'data', 'evidence',
              'error_type', 'error_gap', 'error_detail'}


def _require(condition, message):
    if not condition:
        raise RunStateError(message)


def _sha(path: Path):
    _require(not path.parent.is_symlink(),
             'Collaboration-team evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= 8*1024*1024,
                 'Collaboration-team evidence must be a bounded regular file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024,
             'Collaboration-team evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value):
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


def _sources(root: Path):
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(root: Path):
    _require(root.is_absolute() and not root.is_symlink() and root.is_dir(),
             'Collaboration-team report requires its original owned directory')
    binding = read(root/'collaboration-team-binding.json')
    source = collaboration_team_source_binding()
    _require(binding.get('contract') == 'owned-local-collaboration-team-binding-v1'
             and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID
             and binding.get('case_version') == 1
             and binding.get('runtime_dir') == str(root)
             and binding.get('selected_requirement_ids') == list(REQUIREMENT_IDS)
             and binding.get('adapter_methods') == list(ADAPTER_METHODS)
             and binding.get('store_messages') is False
             and binding.get('source_binding') == source
             and source['parent'] == COLLABORATION_TEAM_PARENT
             and [row['id'] for row in source['requirements']] == list(REQUIREMENT_IDS)
             and isinstance(binding.get('legacy_inventory'), list)
             and binding_matches_reviewed_release(binding['legacy_inventory'])
             and isinstance(binding.get('executor_digest'), str)
             and len(binding['executor_digest']) == 64
             and isinstance(binding.get('bcs_binary_sha256'), str)
             and len(binding['bcs_binary_sha256']) == 64
             and isinstance(binding.get('cli_binary_sha256'), str)
             and len(binding['cli_binary_sha256']) == 64,
             'Collaboration-team frozen binding is invalid')
    return binding


def _evidence_valid(evidence: object, *, merged: bool = False) -> bool:
    if not isinstance(evidence, dict):
        return False
    if (evidence.get('remote_validation') != 'NOT_RUN'
            or evidence.get('grants_release_credit') is not False
            or evidence.get('transport') not in {'PRODUCT_CLI', 'PRODUCT_HTTP'}):
        return False
    if not merged:
        if evidence.get('credential_kind') not in {'owned_local_bot_token', 'human_cookie'}:
            return False
        if evidence.get('transport') == 'PRODUCT_CLI':
            return (evidence.get('exit_status') == 0
                    and isinstance(evidence.get('command_arguments'), list))
        return evidence.get('http_status') == 200
    requests = evidence.get('requests')
    return (isinstance(requests, list) and bool(requests)
            and all(_evidence_valid(request) for request in requests))


def _data_valid(index: int, data: object) -> bool:
    if not isinstance(data, dict):
        return False
    if index == 0:
        return (data.get('customer_service_id') and data.get('protected') is True
                and data.get('restored_public') is False)
    if index == 1:
        return (data.get('request_id') and data.get('requester')
                and data.get('target') == data.get('customer_service_id')
                and data.get('request_status') == 'pending'
                and data.get('rejection_acknowledged') is True
                and data.get('requester_final_list_contains_target') is False
                and data.get('rejected_request_identifiable') is True
                and data.get('restored_public') is True)
    if index == 2:
        participants = data.get('participants')
        return (bool(data.get('group_id') and data.get('driver'))
                and isinstance(participants, list) and len(participants) == 2
                and len(set(participants)) == 2)
    if index == 3:
        participants = data.get('participant_ids')
        return (data.get('group_id') and data.get('added_member')
                and isinstance(participants, list) and len(participants) == 3
                and len(set(participants)) == 3 and data.get('added_member') in participants)
    if index == 4:
        return (data.get('group_id')
                and data.get('cli_contains_group') is True
                and data.get('api_contains_group') is True
                and data.get('cli_returned') == data.get('api_item_count')
                and isinstance(data.get('cli_returned'), int)
                and data.get('cli_returned') > 0)
    if index == 5:
        participants = data.get('requested_participant_bot_ids')
        return (data.get('group_id') and isinstance(participants, list)
                and len(participants) == 3 and len(set(participants)) == 3
                and data.get('recommendation_present') is True)
    if index == 6:
        return data.get('group_id') and data.get('completed') is True
    return (data.get('group_id') and data.get('terminated') is True
            and data.get('completed') is True)


def _business_evidence(root: Path, binding: dict, case: dict) -> bool:
    if not isinstance(case, dict):
        return False
    steps = case.get('steps')
    if (case.get('case_id') != CASE_ID
            or case.get('parent_id') != binding['source_binding']['parent']
            or case.get('status') != 'PASS'
            or case.get('run_id') != binding['run_id']
            or case.get('executor_digest') != binding['executor_digest']
            or case.get('completed_count') != case.get('total_count') != len(REQUIREMENT_IDS)
            or case.get('error_type') is not None
            or not isinstance(steps, list) or len(steps) != len(REQUIREMENT_IDS)):
        return False
    completed_group = terminated_group = None
    for index, step in enumerate(steps):
        if (not isinstance(step, dict) or set(step) != _STEP_KEYS
                or step.get('requirement_id') != REQUIREMENT_IDS[index]
                or step.get('method') != ADAPTER_METHODS[index]
                or step.get('status') != 'PASS' or step.get('error_type') is not None
                or not _evidence_valid(step.get('evidence'), merged=True)
                or not _data_valid(index, step.get('data'))):
            return False
        data = step['data']
        if index in (2, 6):
            completed_group = data.get('group_id')
        if index == 7:
            terminated_group = data.get('group_id')
    return (bool(completed_group) and bool(terminated_group)
            and completed_group != terminated_group)


def _project(root: Path, binding: dict):
    flow = read(root/'collaboration-team-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-collaboration-team.json')
    profile = validate_collaboration_team_profile(read(root/'collaboration-team-profile.json'))
    _require(flow.get('run_id') == binding['run_id']
             and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('case') == case
             and flow.get('shutdown') == runtime
             and flow.get('remote_validation') == 'NOT_RUN'
             and flow.get('full_release_status') == 'NOT_QUALIFIED'
             and flow.get('ready_to_execute') is False
             and flow.get('profile_contract') == profile.get('contract')
             and profile.get('contract') == 'dual-collaboration-team-cli-profile-v1'
             and profile['entrypoint']['origin'] == runtime.get('origin'),
             'Collaboration-team flow differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Collaboration-team runtime binding differs')
    complete = (_business_evidence(root, binding, case)
                and _sources(root) == set(SOURCES))
    stopped = (runtime.get('status') == 'STOPPED'
               and type(runtime.get('returncode')) is int
               and runtime['returncode'] == 0
               and runtime.get('forced_stop') is False)
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if (complete and stopped and cleared
                         and flow.get('status') == 'LOCAL_COLLABORATION_TEAM_PASS') else
              'BLOCKED')
    requirements = [{'id': name, 'status': 'PASS' if status == 'PASS' else 'NOT_RUN'}
                    for name in REQUIREMENT_IDS]
    return {'status': status,
            'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if status == 'PASS' else 'BLOCKED',
            'resource_disposition': RESOURCE_DISPOSITION if complete else 'UNVERIFIED',
            'requirements': requirements,
            'assertions': requirements}


def _summary(binding: dict, projected: dict, issues: list[str]):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID],
                      'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition',
                                                             'UNVERIFIED')}],
            'requirements': projected.get('requirements',
                                          [{'id': name, 'status': 'NOT_RUN'}
                                           for name in REQUIREMENT_IDS]),
            'reports': {'expected_assertion_count': len(REQUIREMENT_IDS),
                        'observed_assertion_count':
                            len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
            'routes': [],
            'cleanup': {'verification_scope': 'COLLABORATION_TEAM_OWNED_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_PERFORMED',
                        'business_history_disposition': 'RETAINED_FOR_DIAGNOSIS'},
            'mock_boundary': {'not_proven': ['remote gateway authentication and authorization',
                                             'Bot engine reasoning',
                                             'actual message delivery',
                                             'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
            'grants_release_credit': False, 'ready_to_execute': False}


def publish_collaboration_team(runtime, binding: dict, flow: dict):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Collaboration-team source changed before report publication')
    _require(read(root/'collaboration-team-flow-report.json') == flow
             and read(root/'owned-bcs.json') == runtime._record(),
             'Collaboration-team publication must follow actual runtime finalization')
    pending = root/'collaboration-team-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in SOURCES}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION,
                  'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'collaboration-team-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root/'collaboration-team-completion.json') == completion_digest,
             'Collaboration-team source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_collaboration_team_report(root: Path):
    _require(not root.is_symlink(), 'Collaboration-team report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'collaboration-team-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Collaboration-team publication is incomplete')
        completion = read(root/'collaboration-team-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Collaboration-team completion binding differs')
        hashes, output_hashes = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(output_hashes) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in output_hashes.items()),
                 'Collaboration-team evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in output_hashes.items())
                 and read(root/'collaboration-team-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Collaboration-team evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Collaboration-team completion, source or output evidence is '
                      'missing, changed or invalid')
    return _summary(binding, projected, issues)
