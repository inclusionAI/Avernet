"""One fresh owned-local runtime lifecycle for the custom-collaboration parent."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import signal
from urllib.parse import quote, urlsplit

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_custom_collaboration_case import (
    CASE_ID,
    REQUIREMENTS,
    SUITE,
    run_custom_collaboration_case,
)
from .local_bot_ws import OwnedLocalBotWebSocket
from .local_fixtures import OwnedLocalFixtures
from .local_custom_collaboration_case import custom_collaboration_source_binding
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier
from .run_state import RunStateError, read, write

ROLES = ('driver', 'member')
BINDING_PATH = 'custom-collaboration-lifecycle-binding.json'
CASE_PATH = 'local-custom-collaboration.json'
FLOW_PATH = 'custom-collaboration-lifecycle-flow-report.json'
DEFINITION_PATH = 'custom-collaboration-definition.yaml'
BINDING_CONTRACT = 'owned-local-custom-collaboration-lifecycle-binding-v1'
SUCCESS_STATUS = 'LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS'

DEFINITION_YAML = (
    'name: Owned-local custom collaboration\n'
    'participants:\n'
    '  planner:\n'
    '    display_name: Release planner\n'
    '    required: true\n'
    '  reviewer:\n'
    '    display_name: Engineering reviewer\n'
    '    required: true\n'
    'runtime:\n'
    '  kind: state_machine\n'
    '  state_machine:\n'
    '    version: 1\n'
    '    graph_mode: acyclic\n'
    '    nodes:\n'
    '      plan:\n'
    '        kind: bot_task\n'
    '        display_name: Plan release\n'
    '        assignee:\n'
    '          type: bot_binding\n'
    '          binding: planner\n'
    '        instruction: Produce a concise release plan.\n'
    '        transitions:\n'
    '          complete:\n'
    '            targets: [review]\n'
    '      review:\n'
    '        kind: bot_task\n'
    '        display_name: Review release\n'
    '        assignee:\n'
    '          type: bot_binding\n'
    '          binding: reviewer\n'
    '        instruction: Review the plan and produce the final recommendation.\n'
    '        final_output: true\n'
)


class _DriverCredential:
    """Keep the pilot driver token only until its single parent-case use."""

    def __init__(self):
        self.bot_id: str | None = None
        self.token: str | None = None
        self.captured = False

    def capture(self, bot_id: str, token: str) -> None:
        if self.captured or not isinstance(bot_id, str) or not isinstance(token, str):
            raise RunStateError('custom-collaboration driver credential capture differs')
        if (not bot_id or not 1 <= len(token) <= 4096
                or not token.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in token)):
            raise RunStateError('custom-collaboration driver credential is invalid')
        self.bot_id, self.token, self.captured = bot_id, token, True

    def inspect(self) -> tuple[str, str]:
        if not self.captured:
            raise RunStateError('custom-collaboration driver credential was not captured')
        return self.bot_id, self.token

    def clear(self) -> None:
        self.token = None

    def summary(self) -> dict:
        state = ('CLEARED' if self.captured and self.token is None
                 else 'HELD' if self.token is not None else 'NOT_CAPTURED')
        return {
            'state': state,
            'bot_id': self.bot_id,
            'credential_memory': 'CLEARED' if self.token is None else 'HELD',
            'memory_cleanup_scope': 'HOLDER_REFERENCES_ONLY',
            'secure_erasure': False,
            'remote_authentication': 'NOT_TESTED',
        }


def _binding(*, binary_sha256: str, cli_binary_sha256: str, run_id: str,
             state_dir: Path, executor: str, source: dict, legacy: list) -> dict:
    return {
        'schema_version': 1,
        'contract': BINDING_CONTRACT,
        'suite': SUITE,
        'case_id': CASE_ID,
        'case_version': 1,
        'run_id': run_id,
        'executor_digest': executor,
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': cli_binary_sha256,
        'store_messages': False,
        'fixture_set': 'pilot',
        'fixture_roles': list(ROLES),
        'fixture_friendship': 'owned-driver-member-request-then-accept',
        'source_binding': source,
        'selected_requirement_ids': list(REQUIREMENTS),
        'legacy_inventory': legacy,
        'runtime_dir': str(state_dir.resolve()),
    }


def _owned_http_json(runtime: OwnedBcsProcess, method: str, path: str,
                     body: dict | None, expected_status: int) -> dict:
    """Call a credential-free owned-local JSON route as the bound Human."""
    target = urlsplit(runtime.inputs.origin)
    if (target.scheme != 'http' or target.hostname != '127.0.0.1'
            or not target.port or target.query or target.fragment):
        raise RunStateError('custom-collaboration owned runtime origin is invalid')
    connection = HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        headers = {'Accept': 'application/json'}
        encoded = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            encoded = json.dumps(body, separators=(',', ':')).encode('utf-8')
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read(65_537)
        if response.status != expected_status or len(raw) > 65_536:
            raise RunStateError('custom-collaboration owned-local admission differs')
        value = json.loads(raw.decode('utf-8'))
        if not isinstance(value, dict):
            raise RunStateError('custom-collaboration owned-local admission is not an object')
        return value
    except (OSError, UnicodeError, ValueError) as failure:
        raise RunStateError('custom-collaboration owned-local admission failed') from failure
    finally:
        connection.close()


def _admit_owned_friend_pair(runtime: OwnedBcsProcess, *, driver_id: str,
                             member_id: str) -> None:
    """Seed the owner-scoped Bot pair required by the legacy trial Story."""
    requested = _owned_http_json(
        runtime, 'POST', '/friends/request',
        {'from_bot': driver_id, 'to_bot': member_id}, 201)
    row = requested.get('data')
    request_id = identifier(row.get('id') if isinstance(row, dict) else None,
                            'custom-collaboration friendship request ID')
    if row.get('from_bot') != driver_id or row.get('to_bot') != member_id:
        raise RunStateError('custom-collaboration owned pair request differs')
    accepted = _owned_http_json(
        runtime, 'POST',
        f'/friends/requests/{quote(request_id, safe="")}/accept', {}, 200)
    if accepted.get('success') is not True:
        raise RunStateError('custom-collaboration owned pair admission differs')

def local_custom_collaboration_run(*, binary: Path, binary_sha256: str,
                                   cli_binary: Path, cli_binary_sha256: str,
                                   state_dir: Path, run_id: str,
                                   confirm_run_id: str, allow_local_processes: bool,
                                   allow_fixture_write: bool, allow_write: bool,
                                   density: bool = False) -> dict:
    """Start, provision, execute, publish, and stop one owned Run.

    The caller separately confirms process, fixture-write and business-write
    authority. A fresh state directory is required so no prior credential,
    process, fixture, Group or report can be adopted.
    """
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError(
            'custom-collaboration requires process/fixture/business permissions '
            'and exact Run confirmation')
    identifier(run_id, 'custom-collaboration Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError(
            'custom-collaboration requires a fresh state directory; '
            'existing evidence is retained')
    executor = executor_digest()
    source = custom_collaboration_source_binding()
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if (source['parent'] not in {row.get('id') for row in reviewed}):
        raise RunStateError(
            'custom-collaboration parent is missing from the release denominator')
    binding = _binding(binary_sha256=binary_sha256,
                       cli_binary_sha256=cli_binary_sha256, run_id=run_id,
                       state_dir=state_dir, executor=executor, source=source,
                       legacy=legacy)

    if density:
        binding['density_enabled'] = True

    runtime = None
    case: dict = {}
    error = None
    credentials = {'credential_memory': 'NOT_CAPTURED'}
    previous = signal.signal(
        signal.SIGTERM,
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id) as owned_runtime:
                runtime = owned_runtime
                write(runtime.root / BINDING_PATH, binding)
                write(runtime.root / 'run.json', {
                    'schema_version': 1, 'status': 'PREPARED', 'run_id': run_id,
                    'ledger_path': str(runtime.root / 'run.sqlite'),
                })
                definition = runtime.root / DEFINITION_PATH
                definition.write_text(DEFINITION_YAML, encoding='utf-8')
                if (not definition.is_file() or definition.is_symlink()
                        or definition.stat().st_size > 1_048_576):
                    raise RunStateError(
                        'collaboration definition must be a bounded owned file')
                credential = _DriverCredential()
                fixtures = OwnedLocalFixtures(runtime)
                try:
                    fixtures.apply(allow_write=True, driver_token_sink=credential.capture)
                    receipt = fixtures.check()
                    roles = receipt.get('roles', {})
                    ids = [row.get('bot_id') for row in roles.values()
                           if isinstance(row, dict)]
                    if (set(roles) != set(ROLES) or len(ids) != len(set(ids))
                            or len(ids) != 2 or any(not item for item in ids)):
                        raise RunStateError(
                            'custom-collaboration requires exactly two distinct owned Bots')
                    driver_id, driver_token = credential.inspect()
                    if roles['driver']['bot_id'] != driver_id:
                        raise RunStateError(
                            'custom-collaboration driver credential is bound to another Bot')
                    _admit_owned_friend_pair(runtime, driver_id=driver_id,
                                               member_id=roles['member']['bot_id'])
                    with OwnedLocalBotWebSocket(runtime.port, driver_id,
                                                driver_token):
                        case = run_custom_collaboration_case(
                            runtime, state_dir=runtime.root, binary=cli_binary,
                            binary_sha256=cli_binary_sha256, yaml_path=definition,
                            driver_id=driver_id, reviewer_id=roles['member']['bot_id'],
                            human_id='human_' + runtime.inputs.human_id,
                            driver_token=driver_token, **({"density": True} if density else {}))
                finally:
                    credential.clear()
                    credentials = credential.summary()
        except BaseException as failure:
            error = failure

        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('custom-collaboration owned runtime was not created')

        case_path = runtime.root / CASE_PATH
        if not case and case_path.is_file():
            saved = read(case_path)
            if (saved.get('case_id') == CASE_ID
                    and saved.get('legacy_id') == source['parent']
                    and saved.get('run_id') == run_id):
                case = saved
        if error is not None and not case:
            case = {'schema_version': 1, 'status': 'BLOCKED', 'case_id': CASE_ID,
                    'legacy_id': source['parent'], 'run_id': run_id,
                    'assertions': [{'id': name, 'status': 'NOT_RUN'}
                                   for name in REQUIREMENTS]}
            write(case_path, case)
        shutdown = runtime._record()
        expected = [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS]
        passed = (error is None and executor_digest() == executor
                  and read(runtime.root / BINDING_PATH) == binding
                  and bool(case)
                  and read(runtime.root / CASE_PATH) == case
                  and case.get('status') == SUCCESS_STATUS
                  and case.get('assertions') == expected
                  and case.get('persistent_group_cleanup') == 'DELETED'
                  and credentials.get('credential_memory') == 'CLEARED'
                  and shutdown.get('status') == 'STOPPED'
                  and type(shutdown.get('returncode')) is int
                  and shutdown.get('returncode') == 0
                  and shutdown.get('forced_stop') is False)
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else SUCCESS_STATUS if passed else 'BLOCKED')
        flow = {
            'schema_version': 1,
            'status': status,
            'run_id': run_id,
            'case_id': CASE_ID,
            'executor_digest': executor,
            'binding_digest': fingerprint(binding),
            'case': case,
            'shutdown': shutdown,
            'credentials': credentials,
            'remote_validation': 'NOT_RUN',
            'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False,
        }
        if error is not None:
            flow['error_type'] = type(error).__name__
        write(runtime.root / FLOW_PATH, flow)
        from .local_custom_collaboration_reporting import publish_custom_collaboration
        canonical = publish_custom_collaboration(runtime, binding, flow)
        flow.update(canonical_summary=canonical['summary'],
                    outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            flow['status'] = 'BLOCKED'
        return flow
    finally:
        signal.signal(signal.SIGTERM, previous)
