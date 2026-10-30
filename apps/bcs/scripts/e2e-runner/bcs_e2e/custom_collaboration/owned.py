"""Real owned-local session-domain observations for the S14 density increment."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit

from ..bounded_process import capture
from ..local_cli import (
    LocalCliError, _custom_json_array, _custom_json_object,
    _execute_custom_collaboration, _parse_trial_group, _private_context,
    _request_trial_json, _trial_graph, _trial_participants, _trial_run,
    create_custom_collaboration_group, custom_collaboration_group_delete_path,
    require_cli_binary, validate_collaboration_yaml,
    TRIAL_CONTEXT, TRIAL_INPUT, TRIAL_TOPIC,
)
from ..run_state import read, write
from .density import definition_evidence, panel_evidence, project


def _trial(runtime, *, state_dir: Path, binary: Path, yaml_path: Path,
           driver_id: str, reviewer_id: str, human_id: str, driver_token: str,
           timeout_seconds: int, events: dict, bindings: dict) -> dict:
    root, context, environment = _private_context(runtime, state_dir)
    group = session = run_id = ''
    deleted = cancelled = False

    def cli(*args):
        raw = _execute_custom_collaboration(binary, environment, root, args, timeout_seconds)
        if driver_token.encode() in raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        return raw

    def obj(*args):
        return _custom_json_object(cli(*args), 'custom collaboration density')

    def http(method, path, body=None):
        return _request_trial_json(runtime, method, path, body, driver_token, 200)

    try:
        write(context, {'token': driver_token, 'bot_uuid': driver_id,
                        'api_base_url': runtime.inputs.origin})
        group, session = _parse_trial_group(cli(
            'create-group', '--driver', driver_id, '--participants',
            f'{driver_id},{reviewer_id}', '--context', TRIAL_CONTEXT,
            '--topic', TRIAL_TOPIC), driver_id, reviewer_id)
        bindings.update(trial_group=group, trial_session=session)
        events['trial'] = {'session_id': session}
        member = obj('session', 'add-member', session, '--bot-uuid', human_id,
                     '--role', 'consultant')
        _trial_participants(member, human_id, 'consultant')
        events['member'] = {'session_id': member.get('session_id') or member.get('id'),
                            'participants': member.get('participants', [])}
        permission = obj('collaborate', 'permission', '--session', session)
        events['permission'] = {k: permission.get(k) for k in
                                ('allowed', 'session_id', 'group_id')}
        if (permission.get('caller_bot_id') != driver_id
                or permission.get('group_owner_bot_id') != driver_id
                or permission.get('policy_version') != 'session_state_machine_v1'
                or permission.get('group_strategy') != 'chat'):
            raise LocalCliError('custom collaboration permission authority differs')
        started = _trial_run(obj('collaborate', 'run', str(yaml_path), '--session', session,
                                '--binding', f'planner={driver_id}', '--binding',
                                f'reviewer={reviewer_id}', '--input', TRIAL_INPUT))
        run_id = started['run_id']
        bindings['run_id'] = run_id
        events['run'] = {k: started.get(k) for k in ('run_id', 'session_id', 'group_id')}
        graph = obj('collaborate', 'query', '--run', run_id, '--graph')
        _trial_graph(graph, run_id, session, group)
        events['graph'] = {'run_id': graph['run']['run_id']}
        pending = _custom_json_array(cli('collaborate', 'query', '--run', run_id, '--pending'),
                                     'custom collaboration pending')
        # Retain only zero-target evidence, not arbitrary remote/user content.
        events['pending'] = [] if pending == [] else {'nonempty': True}
        guard = capture([str(binary), '--json', '--log-level', 'error', 'collaborate',
                         'respond', '--run', run_id, '--node', 'plan', '--content',
                         'invalid Human reply'], cwd=root, environment=environment,
                        timeout_seconds=timeout_seconds, max_output_bytes=65_536,
                        collect_stderr=True)
        if driver_token.encode() in guard.stdout + guard.stderr:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        events['guard'] = {'exit_code': guard.returncode,
                           'not_pending': b'not pending' in guard.stderr}
        history = _custom_json_array(cli('session', 'messages', session, '--view-bot',
                                        driver_id, '--limit', '50'), 'custom collaboration history')
        events['panel'] = panel_evidence(history, run_id)
        cancel = http('POST', f'/state-machine-runs/{quote(run_id, safe="")}/cancel',
                      {'reason': 'E2E one-shot trial completed'})
        # Projector contract (custom_collaboration/density.py) reads the nested
        # run handle, matching the shared station wire shape validated by the
        # dual executor and the legacy local observe path.
        events['cancel'] = {'run': {k: cancel.get('run', {}).get(k)
                                    for k in ('run_id', 'status')}}
        cancelled = (events['cancel']['run']['run_id'] == run_id
                     and events['cancel']['run']['status'] in ('aborted', 'completed', 'failed'))
        preserved = http('GET', f'/sessions/{quote(session, safe="")}')
        events['session'] = {'session_id': preserved.get('session_id') or preserved.get('id'),
                             'group_id': preserved.get('group_id'), 'status': preserved.get('status')}
        removed = http('DELETE', custom_collaboration_group_delete_path(group, driver_id))
        events['trial_deleted'] = {k: removed.get(k) for k in ('deleted', 'id')}
        deleted = removed.get('deleted') is True and removed.get('id') == group
    finally:
        # Only handles created inside this invocation confer cleanup authority.
        # Cleanup errors propagate, but credential context removal still runs.
        try:
            if run_id and not cancelled:
                http('POST', f'/state-machine-runs/{quote(run_id, safe="")}/cancel',
                     {'reason': 'E2E cleanup'})
            if group and not deleted:
                removed = http('DELETE', custom_collaboration_group_delete_path(group, driver_id))
                if removed.get('deleted') is not True or removed.get('id') != group:
                    raise LocalCliError('custom collaboration cleanup is unconfirmed')
        finally:
            write(context, {'token': '', 'bot_uuid': '', 'api_base_url': ''})
            shutil.rmtree(root)
    return {'status': 'LOCAL_CUSTOM_COLLABORATION_TRIAL_PASS',
            'group_id': group, 'session_id': session, 'run_id': run_id,
            'remote_validation': 'NOT_RUN', 'grants_release_credit': False}


def run_density_case(runtime, *, state_dir: Path, binary: Path, binary_sha256: str,
                     yaml_path: Path, driver_id: str, reviewer_id: str, human_id: str,
                     driver_token: str, timeout_seconds: int = 30) -> dict:
    from ..local_custom_collaboration_case import (
        CASE_ID, CUSTOM_COLLABORATION_PARENT, REQUIREMENTS, custom_collaboration_case_plan,
    )
    target = urlsplit(runtime.inputs.origin)
    if (target.scheme != 'http' or target.hostname != '127.0.0.1' or not target.port
            or target.path or target.query or target.fragment):
        raise LocalCliError('density requires an owned loopback runtime')
    require_cli_binary(binary, binary_sha256)
    if (not state_dir.is_absolute() or state_dir.is_symlink()
            or yaml_path.is_symlink() or not yaml_path.resolve().is_relative_to(state_dir)
            or read(state_dir / 'run.json').get('run_id') != runtime.run_id):
        raise LocalCliError('density requires its owned Run journal and definition')
    bindings = {'driver': driver_id, 'reviewer': reviewer_id, 'human': human_id}
    events, base_statuses, operations = {}, {}, {}
    persistent = ''
    persistent_deleted = False
    error = None
    try:
        validate = validate_collaboration_yaml(
            runtime, state_dir=state_dir, case_id=CASE_ID, binary=binary,
            binary_sha256=binary_sha256, yaml_path=yaml_path, timeout_seconds=timeout_seconds)
        events['validate'] = {k: validate[k] for k in ('valid', 'summary')}
        events['definition'] = definition_evidence(yaml_path.read_text())
        base_statuses[REQUIREMENTS[0]] = 'PASS'
        operations['validate'] = validate
        trial = _trial(runtime, state_dir=state_dir, binary=binary, yaml_path=yaml_path,
                       driver_id=driver_id, reviewer_id=reviewer_id, human_id=human_id,
                       driver_token=driver_token, timeout_seconds=timeout_seconds,
                       events=events, bindings=bindings)
        operations['trial'] = trial
        base_statuses[REQUIREMENTS[2]] = 'PASS'
        created = create_custom_collaboration_group(
            runtime, state_dir=state_dir, case_id=CASE_ID, binary=binary,
            binary_sha256=binary_sha256, yaml_path=yaml_path, driver_id=driver_id,
            reviewer_id=reviewer_id, driver_token=driver_token, timeout_seconds=timeout_seconds)
        persistent = created['group_id']
        bindings['persistent_group'] = persistent
        events['persistent'] = {'id': persistent, 'participants': created['participants']}
        events['persistent_read'] = {'id': persistent, 'group_strategy': created['group_strategy']}
        removed = _request_trial_json(runtime, 'DELETE',
                                      custom_collaboration_group_delete_path(persistent, driver_id),
                                      None, driver_token, 200)
        events['persistent_deleted'] = {k: removed.get(k) for k in ('deleted', 'id')}
        persistent_deleted = removed.get('deleted') is True and removed.get('id') == persistent
        if not persistent_deleted:
            raise LocalCliError('custom collaboration persistent cleanup differs')
        operations['create-persistent-group'] = {**created, 'group_cleanup': 'DELETED'}
        base_statuses[REQUIREMENTS[1]] = 'PASS'
    except BaseException as failure:
        error = failure
    finally:
        if persistent and not persistent_deleted:
            try:
                removed = _request_trial_json(runtime, 'DELETE',
                    custom_collaboration_group_delete_path(persistent, driver_id), None,
                    driver_token, 200)
                if removed.get('deleted') is not True or removed.get('id') != persistent:
                    raise LocalCliError('custom collaboration cleanup unconfirmed')
            except BaseException as cleanup_failure:
                error = cleanup_failure
    density = project(events, bindings, base_statuses)
    passed = error is None and density['status'] == 'PASS'
    result = {
        'schema_version': 1,
        'status': 'LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS' if passed else 'BLOCKED',
        'case_id': CASE_ID, 'legacy_id': CUSTOM_COLLABORATION_PARENT, 'run_id': runtime.run_id,
        'assertions': [{'id': row['id'], 'status': row['status']}
                       for row in density['requirements']],
        'selected_requirement_ids': list(REQUIREMENTS), 'parent_requirement_count': 3,
        'full_parent_coverage': 'LOCAL_PARENT_REQUIREMENTS_ONLY', 'operations': operations,
        'persistent_group_cleanup': 'DELETED' if persistent_deleted else 'UNCONFIRMED',
        'binary_sha256': binary_sha256, 'yaml_path': str(yaml_path),
        'driver_bot': driver_id, 'human_participant': human_id,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'legacy_counts': custom_collaboration_case_plan(), 'density': density,
        'error_type': type(error).__name__ if error else None,
    }
    write(state_dir / 'local-custom-collaboration.json', result)
    if error is not None:
        raise error
    if not passed:
        raise LocalCliError('custom collaboration density assertions failed')
    return result
