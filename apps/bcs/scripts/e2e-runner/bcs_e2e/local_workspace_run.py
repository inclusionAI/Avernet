"""Owned-local lifecycle for the first Workspace API obligations.

This is a reusable parent-Story increment, not full Workspace qualification.
No remote Profile, legacy shell orchestration, or automatic deletion is exposed.
"""
from __future__ import annotations

from pathlib import Path
import signal

from .errors import has_cancellation
from .inventory import ROOT, current_manifest, executor_digest
from .local_fixtures import OwnedLocalFixtures
from .local_role_identity import OwnedLocalRoleIdentity
from .migration_inventory import validate_contract
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier
from .run_state import RunStateError, read, write

CASE_ID = 'LOCAL-WORKSPACE-API-01'
PARENT = 'story_user_operates_group_workspace'
REQUIREMENTS = tuple('WORKSPACE.' + name for name in
                     ('create', 'detail', 'patch', 'list', 'members', 'label', 'visibility'))
CLI_REQUIREMENTS = tuple('WORKSPACE.' + name for name in
                         ('cli-create-get', 'cli-add-member', 'cli-fuse', 'cli-status'))
INCIDENT_REQUIREMENTS = tuple('WORKSPACE.' + name for name in
                              ('incident-group', 'participant-mode', 'workspace', 'routing-service'))

MESSAGE_REQUIREMENTS = ('WORKSPACE.messages',)
DELETE_REQUIREMENTS = ('WORKSPACE.incident-delete',)

def local_workspace_api_run(*, binary: Path, binary_sha256: str, state_dir: Path,
                            run_id: str, confirm_run_id: str, allow_local_processes: bool,
                            allow_fixture_write: bool, allow_write: bool,
                            cli_binary: Path | None = None, cli_binary_sha256: str | None = None,
                            with_incident: bool = False, with_messages: bool = False,
                            with_delete: bool = False) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Workspace API requires process, fixture and business permissions plus exact Run confirmation')
    if (cli_binary is None) != (cli_binary_sha256 is None):
        raise RunStateError('Workspace CLI requires both an approved binary and its SHA256')
    if type(with_delete) is not bool:
        raise RunStateError('Workspace deletion selection must be boolean')
    if with_delete:
        with_messages = True
    if type(with_messages) is not bool:
        raise RunStateError('Workspace message selection must be boolean')
    if with_messages:
        with_incident = True
    with_cli = cli_binary is not None
    if type(with_incident) is not bool or with_incident and not with_cli:
        raise RunStateError('Workspace incident configuration requires the complete owned API/CLI setup')
    if with_cli:
        from .local_cli import require_cli_binary
        require_cli_binary(cli_binary, cli_binary_sha256)
    selected = REQUIREMENTS + (CLI_REQUIREMENTS if with_cli else ()) + (INCIDENT_REQUIREMENTS if with_incident else ()) + (MESSAGE_REQUIREMENTS if with_messages else ()) + (DELETE_REQUIREMENTS if with_delete else ())
    result_case_id = 'LOCAL-WORKSPACE-API-CLI-01' if with_cli else CASE_ID
    if with_incident:
        result_case_id = 'LOCAL-WORKSPACE-CONFIG-01'
    if with_messages:
        result_case_id = 'LOCAL-WORKSPACE-MESSAGING-01'
    if with_delete:
        result_case_id = 'LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01'
    identifier(run_id, 'Workspace Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Workspace API requires a fresh owned state directory')
    contract = validate_contract(read(ROOT/'migration-contracts.json'), source_root=ROOT.parent/'e2e-test')
    parent = next(story for story in contract['stories'] if story['legacy_id'] == PARENT)
    parent_ids = [item['id'] for item in parent['requirements']]
    if parent_ids[:len(selected)] != list(selected):
        raise RunStateError('Workspace parent API obligations changed; review the migration mapping')
    source = executor_digest()
    binding = {'schema_version': 1, 'contract': 'owned-local-workspace-api-v1',
               'case_id': CASE_ID, 'run_id': run_id, 'executor_digest': source,
               'bcs_binary_sha256': binary_sha256, 'parent': parent,
               'migration_contract_digest': fingerprint(contract),
               'legacy_inventory': current_manifest()['legacy_inventory'],
               'selected_requirement_ids': list(selected)}
    if with_cli:
        binding.update(contract='owned-local-workspace-api-cli-v1',
                       case_id=result_case_id, cli_binary_sha256=cli_binary_sha256)
    if with_incident:
        binding.update(contract='owned-local-workspace-config-v1', case_id=result_case_id)
    if with_messages:
        binding.update(contract='owned-local-workspace-messaging-v1', case_id=result_case_id, store_messages=True)
    if with_delete:
        from .local_workspace_reporting import frozen_inventory
        binding.update(report_inventory=frozen_inventory(), contract='owned-local-workspace-report-binding-v1', case_id=result_case_id,
                       suite='owned-local-workspace-v1', runtime_dir=str(state_dir.resolve()))
    runtime = None
    identities = None
    case = {}
    cli_case = {}
    incident_case = {}
    message_case = {}
    delete_case = {}
    density_roles = None
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir.resolve(), run_id=run_id, store_messages=with_messages) as runtime:
                write(runtime.root/'workspace-binding.json', binding)
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    for role in fixtures.roles:
                        identities.verify(role)
                    # Snapshot role IDs while the identity context is open: close()
                    # clears its entries, and density bindings need IDs only.
                    density_roles = identities.role_bot_ids()
                    from .local_workspace_api import OwnedLocalWorkspaceApi
                    case = OwnedLocalWorkspaceApi(identities).run()
                    if with_cli:
                        from .local_workspace_context import prepare_workspace_context
                        from .local_workspace_cli import OwnedLocalWorkspaceCli
                        prepare_workspace_context(identities)
                        cli_case = OwnedLocalWorkspaceCli(identities, cli_binary, cli_binary_sha256).run()
                    if with_incident:
                        from .local_workspace_incident import OwnedLocalWorkspaceIncident
                        incident_case = OwnedLocalWorkspaceIncident(identities).run()
                    if with_messages:
                        from .local_workspace_messages import OwnedLocalWorkspaceMessages
                        message_execution = OwnedLocalWorkspaceMessages(identities)
                        message_case = message_execution.run()
                    if with_delete:
                        from .local_workspace_delete import OwnedLocalWorkspaceDelete
                        delete_case = OwnedLocalWorkspaceDelete(message_execution).run()

        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Workspace runtime did not start')
        if not case and (runtime.root/'local-workspace-api.json').is_file():
            saved = read(runtime.root/'local-workspace-api.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        if with_cli and not cli_case and (runtime.root/'local-workspace-cli.json').is_file():
            saved = read(runtime.root/'local-workspace-cli.json')
            if (saved.get('case_id') == 'LOCAL-WORKSPACE-CLI-01' and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                cli_case = saved
        if with_incident and not incident_case and (runtime.root/'local-workspace-incident.json').is_file():
            saved = read(runtime.root/'local-workspace-incident.json')
            if (saved.get('case_id') == 'LOCAL-WORKSPACE-INCIDENT-01' and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                incident_case = saved
        if with_messages and not message_case and (runtime.root/'local-workspace-messages.json').is_file():
            saved = read(runtime.root/'local-workspace-messages.json')
            if (saved.get('case_id') == 'LOCAL-WORKSPACE-MESSAGES-01' and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                message_case = saved
        if with_delete and not delete_case and (runtime.root/'local-workspace-delete.json').is_file():
            saved = read(runtime.root/'local-workspace-delete.json')
            if (saved.get('case_id') == 'LOCAL-WORKSPACE-DELETE-01' and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                delete_case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities is not None else {
            'credential_memory': 'NOT_CAPTURED'}
        expected = [{'id': value, 'status': 'PASS'} for value in REQUIREMENTS]
        bound = (executor_digest() == source and read(runtime.root/'workspace-binding.json') == binding)
        journal_matches = bool(case) and read(runtime.root/'local-workspace-api.json') == case
        passed = (error is None and bound and journal_matches and case.get('status') == 'LOCAL_WORKSPACE_API_PASS'
                  and case.get('assertions') == expected and case.get('run_id') == run_id
                  and case.get('case_id') == CASE_ID
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED')
        if with_cli:
            passed = (passed and bool(cli_case) and cli_case.get('status') == 'LOCAL_WORKSPACE_CLI_PASS'
                      and cli_case.get('run_id') == run_id and cli_case.get('case_id') == 'LOCAL-WORKSPACE-CLI-01'
                      and cli_case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in CLI_REQUIREMENTS]
                      and read(runtime.root/'local-workspace-cli.json') == cli_case)
        if with_incident:
            passed = (passed and bool(incident_case)
                      and incident_case.get('status') == 'LOCAL_WORKSPACE_INCIDENT_CONFIG_PASS'
                      and incident_case.get('run_id') == run_id
                      and incident_case.get('case_id') == 'LOCAL-WORKSPACE-INCIDENT-01'
                      and incident_case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in INCIDENT_REQUIREMENTS]
                      and read(runtime.root/'local-workspace-incident.json') == incident_case)
        if with_messages:
            passed = (passed and message_case.get('status') == 'LOCAL_WORKSPACE_MESSAGES_PASS'
                      and message_case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in MESSAGE_REQUIREMENTS]
                      and message_case.get('case_id') == 'LOCAL-WORKSPACE-MESSAGES-01'
                      and message_case.get('run_id') == run_id and message_case.get('receiver_state') == 'CLOSED'
                      and read(runtime.root/'local-workspace-messages.json') == message_case)
        if with_delete:
            passed = (passed and delete_case.get('status') == 'LOCAL_WORKSPACE_DELETE_PASS'
                      and delete_case.get('assertions') == [{'id': name, 'status': 'PASS'} for name in DELETE_REQUIREMENTS]
                      and delete_case.get('case_id') == 'LOCAL-WORKSPACE-DELETE-01'
                      and delete_case.get('run_id') == run_id
                      and read(runtime.root/'local-workspace-delete.json') == delete_case)
        # Density seam (G1): frozen 63-atom projection over the retained
        # journals. Purely observational — the seventeen-obligation gate above
        # is untouched, and a projector failure is recorded as ERROR, never
        # used to excuse or repair executor status.
        from .group_workspace_density import (density_summary,
                                               load_group_workspace_density_contract,
                                               project_density,
                                               workspace_density_bindings)
        journals = {'api': case, 'cli': cli_case, 'incident': incident_case,
                    'messages': message_case, 'delete': delete_case}
        try:
            density = project_density(
                load_group_workspace_density_contract(), journals,
                workspace_density_bindings(identities, journals, roles=density_roles))
        except Exception as density_failure:
            density = {'schema_version': 1, 'status': 'ERROR',
                       'error_type': type(density_failure).__name__,
                       'error_message': str(density_failure)}
        status = ('CANCELLED' if error is not None and has_cancellation(error) else
                  ('LOCAL_WORKSPACE_INCIDENT_ROUNDTRIP_PASS' if with_delete else
                   'LOCAL_WORKSPACE_MESSAGING_PASS' if with_messages else
                   'LOCAL_WORKSPACE_CONFIG_PASS' if with_incident else
                   'LOCAL_WORKSPACE_API_CLI_PASS' if with_cli else 'LOCAL_WORKSPACE_API_PASS') if passed else 'BLOCKED')
        # Errors after writes remain in the API's original journal. Never repair
        # its status or infer a successful obligation from partial API evidence.
        observed = {item['id']: item['status'] for item in case.get('assertions', []) + cli_case.get('assertions', []) + incident_case.get('assertions', []) + message_case.get('assertions', []) + delete_case.get('assertions', [])
                    if isinstance(item, dict) and item.get('id') in selected
                    and item.get('status') in ('PASS', 'FAIL')}
        result = {'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': result_case_id,
                  'executor_digest': source, 'case': case, 'shutdown': shutdown,
                  'credentials': credentials,
                  'requirements': [{'id': name, 'status': observed.get(name, 'NOT_RUN')}
                                   for name in parent_ids],
                  'selected_requirement_count': len(selected),
                  'parent_requirement_count': len(parent_ids),
                  'legacy_inventory_count': len(binding['legacy_inventory']),
                  'full_story_status': 'NOT_MIGRATED', 'full_release_status': 'NOT_QUALIFIED',
                  'remaining_parent_steps': 'CLI_MESSAGES_INCIDENT_DELETE_NOT_RUN',
                  'resource_disposition': 'RETAINED_LOCAL_DATABASE',
                  'automatic_delete': False, 'remote_validation': 'NOT_RUN',
                  'api_journal': 'local-workspace-api.json',
                  'scope_note': 'Local API increment only; no CLI, incident, messaging or deletion qualification',
                  'ready_to_execute': False,
                  'density': density, 'density_summary': density_summary(density)}
        if with_cli:
            result.update(cli_case=cli_case, cli_journal='local-workspace-cli.json',
                          remaining_parent_steps='INCIDENT_MESSAGES_DELETE_NOT_RUN',
                          scope_note='Local API and CLI increment; full Workspace Story not qualified',
                          fusion_mode='PRODUCT_LOCAL_SIMPLE_FUSION_NO_LLM')
        if with_incident:
            result.update(incident_case=incident_case, incident_journal='local-workspace-incident.json',
                          remaining_parent_steps='MESSAGES_INCIDENT_DELETE_NOT_RUN',
                          scope_note='Local API, CLI and incident configuration; full Story not qualified')
        if with_messages:
            result.update(message_case=message_case, message_journal='local-workspace-messages.json',
                          remaining_parent_steps='INCIDENT_DELETE_NOT_RUN',
                          scope_note='Local API, CLI, incident configuration and transport reception; no engine reasoning or full Story qualification')
        if with_delete:
            result.update(delete_case=delete_case, delete_journal='local-workspace-delete.json',
                          remaining_parent_steps=('NONE_CANONICAL_REPORTING_PENDING' if delete_case.get('status') == 'LOCAL_WORKSPACE_DELETE_PASS'
                                                  else 'LOCAL_ROUNDTRIP_INCOMPLETE'),
                          resource_disposition=('INCIDENT_GROUP_SESSION_DELETED_HISTORY_AND_OTHER_GROUPS_RETAINED'
                                                if delete_case.get('status') == 'LOCAL_WORKSPACE_DELETE_PASS'
                                                else 'INCIDENT_DELETION_UNCONFIRMED_OTHER_RESOURCES_RETAINED'),
                          scope_note='All seventeen local Workspace obligations executed; canonical integration done, remote qualification pending')
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/('workspace-incident-roundtrip-report.json' if with_delete else
                                 'workspace-messaging-report.json' if with_messages else
                                 'workspace-config-report.json' if with_incident else
                                 'workspace-api-cli-report.json' if with_cli else 'workspace-api-report.json'), result)
        if with_delete:
            from .local_workspace_reporting import publish_workspace
            canonical = publish_workspace(runtime, binding, result)
            result = {**result, 'canonical_status': canonical['status'],
                      'canonical_summary': canonical['summary'], 'outputs': canonical['outputs']}
            if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
                result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
