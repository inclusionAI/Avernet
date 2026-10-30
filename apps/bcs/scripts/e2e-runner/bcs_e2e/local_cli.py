"""Product CLI session-read slice for a live owned local runtime only.

This is not a remote credential adapter or a replacement for the CLI release
suite. Legacy Session read is public. The optional permission read consumes only a
newly created local driver credential through an isolated temporary context.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import shutil
import stat
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .bounded_process import ProcessCaptureError, capture
from .errors import primary_error
from .ledger import Ledger
from .local_auth import OwnedLocalAuth
from .local_operator_cli_contract import custom_collaboration_source_binding
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import identifier, unique_keys
from .run_state import read, require_profile_binding, write


class LocalCliError(RuntimeError):
    pass


def require_cli_binary(binary: Path, binary_sha256: str) -> None:
    """Validate the explicitly selected product artifact before any startup."""
    if (not isinstance(binary_sha256, str) or len(binary_sha256) != 64
            or any(c not in '0123456789abcdef' for c in binary_sha256)):
        raise LocalCliError('product CLI requires an explicit SHA256')
    if (not binary.is_absolute() or binary.is_symlink() or not binary.is_file()
            or not stat.S_ISREG(binary.stat().st_mode) or not os.access(binary, os.X_OK)
            or file_sha256(binary) != binary_sha256):
        raise LocalCliError('product CLI executable or digest is not approved')


def _execute(binary: Path, environment: dict[str, str], root: Path, session_id: str,
             timeout: int, output_limit: int = 65_536, *, permission: bool = False) -> bytes:
    """Drain both pipes within one budget; never retain arbitrary stderr."""
    try:
        arguments = (['collaborate', 'permission', '--session', session_id] if permission
                     else ['session', 'get', session_id])
        result = capture([str(binary), '--json', '--log-level', 'error', *arguments],
                         cwd=root, environment=environment, timeout_seconds=timeout,
                         max_output_bytes=output_limit)
    except ProcessCaptureError as error:
        public_error = LocalCliError(str(error))
        if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
            public_error.child_shutdown_status = "UNCONFIRMED"
        raise public_error from None
    if result.returncode != 0:
        raise LocalCliError('product CLI returned a nonzero exit status')
    return result.stdout


def _private_context(runtime: OwnedBcsProcess, state_dir: Path) -> tuple[Path, Path, dict[str, str]]:
    if state_dir.is_symlink():
        raise LocalCliError('product CLI state directory must not be a symlink')
    root = state_dir / ('product-cli-' + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    for name in ('home', 'bot', 'tmp'):
        (root / name).mkdir(mode=0o700)
    (root / 'bot' / '.bcs').mkdir(mode=0o700)
    # Consume the real product context resolution rather than a URL flag.
    context = root / 'bot' / '.bcs' / 'session.json'
    write(context, {'token': '', 'api_base_url': runtime.inputs.origin})
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'home'),
                   'BOT_DATA_DIR': str(root / 'bot'), 'TMPDIR': str(root / 'tmp'),
                   'LANG': 'C', 'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production'}
    # tc_sdb_nenv selects the product's no-Office-OAuth path, NOT the BCS
    # data environment. Never inherit tokens, proxies or the server key.
    return root, context, environment


class OwnedLocalSessionCli:
    def __init__(self, runtime: OwnedBcsProcess, profile: dict, *, state_dir: Path,
                 case_id: str, binary: Path, binary_sha256: str, timeout_seconds: int = 10):
        if not state_dir.is_absolute() or state_dir.is_symlink() or not state_dir.is_dir():
            raise LocalCliError('product CLI requires an absolute regular state directory')
        identifier(case_id, 'CLI case ID')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise LocalCliError('product CLI timeout must be 1..30 seconds')
        self.binary, self.binary_sha256 = binary, binary_sha256
        self._require_binary()
        self.binary = binary.resolve()
        self.auth = OwnedLocalAuth(runtime, profile)
        self.runtime, self.profile = runtime, profile
        self.state_dir, self.case_id, self.timeout = state_dir, case_id, timeout_seconds
        state = read(state_dir / 'run.json')
        self.digest = require_profile_binding(state, profile)
        if state['run_id'] != runtime.run_id or Path(state['ledger_path']) != state_dir / 'run.sqlite':
            raise LocalCliError('product CLI Run/ledger binding mismatch')
        self.run_id = runtime.run_id

    def _require_binary(self) -> None:
        require_cli_binary(self.binary, self.binary_sha256)

    def _require_session(self, session_id: str, group_id: str) -> None:
        self.auth.require_bound(self.profile)
        state = read(self.state_dir / 'run.json')
        if (state.get('run_id') != self.run_id or require_profile_binding(state, self.profile) != self.digest
                or Path(state['ledger_path']) != self.state_dir / 'run.sqlite'):
            raise LocalCliError('product CLI Run binding changed')
        with Ledger(self.state_dir / 'run.sqlite', self.run_id, self.digest, read_only=True) as ledger:
            rows = ledger.resource_snapshot()
        sessions = [row for row in rows if row['kind'] == 'session' and row['resource_id'] == session_id]
        groups = [row for row in rows if row['kind'] == 'group' and row['resource_id'] == group_id]
        if len(sessions) != 1 or len(groups) != 1:
            raise LocalCliError('product CLI requires exact current-Run resources')
        session, group = sessions[0], groups[0]
        if (session['parent'] != group['handle'] or group['parent'] is not None
                or any(row['origin'] != 'created' or row['state'] != 'live'
                       or row['case_id'] != self.case_id or row['attempt'] != 1 for row in (session, group))):
            raise LocalCliError('product CLI resources are not live owned case resources')

    def read_session(self, session_id: str, group_id: str, expected_title: str) -> dict:
        identifier(session_id, 'CLI session ID')
        identifier(group_id, 'CLI group ID')
        if not isinstance(expected_title, str) or not expected_title:
            raise LocalCliError('product CLI requires the expected test session title')
        self._require_session(session_id, group_id)
        self._require_binary()
        root, context, environment = self._context()
        started = time.monotonic()
        try:
            raw = _execute(self.binary, environment, root, session_id, self.timeout)
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if (not isinstance(value, dict) or value.get('id') != session_id
                    or value.get('session_id') != session_id or value.get('group_id') != group_id
                    or value.get('session_title') != expected_title or value.get('status') != 'running'):
                raise LocalCliError('product CLI session response does not match the owned expectation')
            self._require_session(session_id, group_id)
            self._require_binary()
        except (UnicodeError, ValueError):
            raise LocalCliError('product CLI did not return one valid JSON object') from None
        except OSError as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            public_error = LocalCliError('product CLI process or private context failed')
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                public_error.child_shutdown_status = 'UNCONFIRMED'
            raise public_error from None
        return {'status': 'LOCAL_CLI_SLICE_PASS', 'operation': 'cli.session.get',
                'source': 'product-bcs-cli', 'api_face': 'legacy', 'authentication': 'NONE',
                'binary_sha256': self.binary_sha256, 'group_id': group_id, 'session_id': session_id,
                'context_dir': str(root), 'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
                'full_cli_coverage': 'NOT_QUALIFIED', 'remote_authentication': 'NOT_TESTED'}

    def _context(self):
        return _private_context(self.runtime, self.state_dir)

    def read_permission(self, session_id: str, group_id: str, bot_id: str, bot_token: str) -> dict:
        """One authenticated GET, never a state-machine start or remote binding."""
        identifier(session_id, 'CLI session ID')
        identifier(group_id, 'CLI group ID')
        if bot_id != self.profile['actors']['driver']['bot_id']:
            raise LocalCliError('product CLI credential is not bound to the local driver')
        if (not isinstance(bot_token, str) or not 1 <= len(bot_token) <= 4096
                or not bot_token.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in bot_token)):
            raise LocalCliError('product CLI local driver credential is missing or invalid')
        self._require_session(session_id, group_id)
        self._require_binary()
        root, context, environment = self._context()
        expected = {'session_id': session_id, 'group_id': group_id, 'caller_bot_id': bot_id,
                    'group_owner_bot_id': bot_id, 'allowed': True, 'reason_code': 'allowed',
                    'policy_version': 'session_state_machine_v1', 'group_strategy': 'chat'}
        started = time.monotonic()
        try:
            write(context, {'token': bot_token, 'bot_uuid': bot_id, 'api_base_url': self.runtime.inputs.origin})
            raw = _execute(self.binary, environment, root, session_id, self.timeout, permission=True)
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if (not isinstance(value, dict) or value.get('allowed') is not True
                    or any(value.get(key) != expected_value for key, expected_value in expected.items())
                    or value.get('active_run_id') is not None):
                raise LocalCliError('product CLI permission response differs from the owned expectation')
            self._require_session(session_id, group_id)
            self._require_binary()
        except (UnicodeError, ValueError):
            raise LocalCliError('product CLI permission did not return one valid JSON object') from None
        except OSError as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            failure = LocalCliError('product CLI permission process or context failed')
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            # Scrub only this invocation's private credential file, not the
            # operator home or arbitrary output. A failure cannot become PASS.
            try:
                write(context, {'token': '', 'api_base_url': self.runtime.inputs.origin})
            except BaseException as error:
                error.credential_cleanup = 'UNCONFIRMED'
                error.credential_context = str(root)
                raise
        return {**expected, 'status': 'LOCAL_CLI_AUTH_PASS', 'operation': 'cli.collaborate.permission',
                'source': 'product-bcs-cli', 'api_face': 'legacy', 'authentication': 'OWNED_LOCAL_BOT',
                'binary_sha256': self.binary_sha256, 'credential_cleanup': 'CLEARED',
                'context_dir': str(root), 'elapsed_ms': round((time.monotonic()-started)*1000, 2),
                'remote_authentication': 'NOT_TESTED', 'full_cli_coverage': 'NOT_QUALIFIED'}


CUSTOM_COLLABORATION_CONTEXT = "Review release readiness through a custom workflow"
CUSTOM_COLLABORATION_TOPIC = "CLI custom release workflow"


def _execute_custom_collaboration(binary: Path, environment: dict[str, str], root: Path,
                                    arguments: tuple[str, ...], timeout: int) -> bytes:
    try:
        result = capture([str(binary), '--json', '--log-level', 'error', *arguments],
                         cwd=root, environment=environment, timeout_seconds=timeout,
                         max_output_bytes=65_536)
    except ProcessCaptureError as error:
        public_error = LocalCliError(str(error))
        if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
            public_error.child_shutdown_status = 'UNCONFIRMED'
        raise public_error from None
    if result.returncode != 0:
        raise LocalCliError('product CLI returned a nonzero exit status')
    return result.stdout


def create_custom_collaboration_group(runtime: OwnedBcsProcess, *, state_dir: Path,
                                       case_id: str, binary: Path, binary_sha256: str,
                                       yaml_path: Path, driver_id: str, reviewer_id: str,
                                       driver_token: str, timeout_seconds: int = 30) -> dict:
    """Create and inspect one owned-local custom collaboration Group.

    This executes only the source obligation for the persistent custom-collaboration
    Group path. It does not claim remote validation, full parent-story closure, or
    release qualification.
    """
    if (not state_dir.is_absolute() or state_dir.is_symlink() or not state_dir.is_dir()):
        raise LocalCliError('product CLI requires an absolute regular state directory')
    identifier(case_id, 'CLI case ID')
    identifier(driver_id, 'custom collaboration driver Bot ID')
    identifier(reviewer_id, 'custom collaboration reviewer Bot ID')
    identifier(driver_token, 'custom collaboration driver Bot credential')
    if driver_id == reviewer_id:
        raise LocalCliError('custom collaboration requires two distinct Bots')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise LocalCliError('product CLI timeout must be 1..30 seconds')
    binding = custom_collaboration_source_binding()
    if binding['requirements'][1]['id'] != 'CUSTOM-COLLABORATION.create-persistent-group':
        raise LocalCliError('custom collaboration source binding changed')
    if not isinstance(yaml_path, Path):
        raise LocalCliError('collaboration YAML must be an absolute owned-state file path')
    if yaml_path.is_symlink():
        raise LocalCliError('collaboration YAML must not be a symlink')
    yaml_path = yaml_path.resolve()
    if (not yaml_path.is_file() or yaml_path.stat().st_size > 1_048_576
            or not yaml_path.is_relative_to(state_dir.resolve())):
        raise LocalCliError('collaboration YAML must be a finite regular file inside the owned Run state')
    state = read(state_dir / 'run.json')
    if (state.get('run_id') != runtime.run_id
            or not isinstance(state.get('ledger_path'), str)
            or Path(state['ledger_path']) != state_dir / 'run.sqlite'):
        raise LocalCliError('product CLI Run/ledger binding mismatch')
    require_cli_binary(binary, binary_sha256)
    binary = binary.resolve()
    root, context, environment = _private_context(runtime, state_dir)
    started = time.monotonic()
    try:
        write(context, {'token': driver_token, 'bot_uuid': driver_id,
                        'api_base_url': runtime.inputs.origin})
        create_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('collaboration', 'create', str(yaml_path), '--driver', driver_id,
             '--binding', f'planner={driver_id}', '--binding', f'reviewer={reviewer_id}',
             '--context', CUSTOM_COLLABORATION_CONTEXT,
             '--topic', CUSTOM_COLLABORATION_TOPIC), timeout_seconds)
        try:
            created = json.loads(create_raw.decode('utf-8'), object_pairs_hook=unique_keys)
        except (UnicodeError, ValueError):
            raise LocalCliError('product CLI did not return one valid JSON object') from None
        if not isinstance(created, dict):
            raise LocalCliError('product CLI did not return one valid JSON object')
        group_id = created.get('id')
        session_id = created.get('session_id')
        participants = created.get('participants')
        if (not isinstance(group_id, str) or not group_id
                or created.get('driver_bot') != driver_id
                or not isinstance(participants, list)
                or any(not isinstance(item, str) for item in participants)
                or set(participants) != {driver_id, reviewer_id}
                or len(participants) != 2
                or created.get('created') is not True):
            raise LocalCliError('custom collaboration Group response differs from the owned expectation')
        try:
            session_id = identifier(session_id, 'custom collaboration initial Session ID')
        except ValueError as error:
            raise LocalCliError(str(error)) from None
        if driver_token.encode() in create_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        read_raw = _execute_custom_collaboration(
            binary, environment, root, ('get-group', '--id', group_id), timeout_seconds)
        try:
            read_group = json.loads(read_raw.decode('utf-8'), object_pairs_hook=unique_keys)
        except (UnicodeError, ValueError):
            raise LocalCliError('custom collaboration Group read did not return one JSON object') from None
        if not isinstance(read_group, dict):
            raise LocalCliError('custom collaboration Group read did not return one JSON object')
        read_participants = read_group.get('participants')
        bot_ids = []
        if isinstance(read_participants, list):
            for item in read_participants:
                if isinstance(item, dict):
                    bot_ids.append(item.get('bot_uuid'))
                elif isinstance(item, str):
                    bot_ids.append(item)
        if (read_group.get('id') != group_id
                or read_group.get('driver_bot') != driver_id
                or read_group.get('group_strategy') != 'state_machine'
                or sorted(bot_ids) != sorted([driver_id, reviewer_id])
                or len(bot_ids) != 2):
            raise LocalCliError('custom collaboration Group readback differs from the owned expectation')
        if driver_token.encode() in read_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
    except BaseException as operation_error:
        cleanup_error = None
        try:
            write(context, {'token': '', 'bot_uuid': '', 'api_base_url': ''})
            shutil.rmtree(root)
        except BaseException as error:
            cleanup_error = error
        if cleanup_error is not None or root.exists():
            failure = LocalCliError('custom collaboration context cleanup is unconfirmed')
            failure.credential_context = str(root)
            failure.credential_cleanup = 'UNCONFIRMED'
            raise failure from operation_error
        raise
    else:
        cleanup_error = None
        try:
            write(context, {'token': '', 'bot_uuid': '', 'api_base_url': ''})
            shutil.rmtree(root)
        except BaseException as error:
            cleanup_error = error
        if cleanup_error is not None or root.exists():
            failure = LocalCliError('custom collaboration context cleanup is unconfirmed')
            failure.credential_context = str(root)
            failure.credential_cleanup = 'UNCONFIRMED'
            raise failure from cleanup_error
    from .local_custom_collaboration_case import project_density
    density = project_density("create_persistent_group", created, driver_id=driver_id,
                              reviewer_id=reviewer_id)
    density.update(project_density("read_persistent_group", read_group, group_id=group_id))
    return {'density_observations': density,
            'status': 'LOCAL_CUSTOM_COLLABORATION_CREATE_GROUP_PASS',
            'case_id': case_id,
            'parent_requirement_id': binding['requirements'][1]['id'],
            'operation': 'cli.collaboration.create.get-group',
            'source': 'product-bcs-cli', 'authentication': 'OWNED_LOCAL_BOT',
            'binary_sha256': binary_sha256, 'yaml_path': str(yaml_path),
            'context': CUSTOM_COLLABORATION_CONTEXT, 'topic': CUSTOM_COLLABORATION_TOPIC,
            'group_id': group_id, 'session_id': session_id, 'driver_bot': driver_id,
            'participants': sorted([driver_id, reviewer_id]),
            'group_strategy': 'state_machine',
            'context_cleanup': 'REMOVED',
            'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
            'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            'full_parent_coverage': 'NOT_RUN'}


def _custom_json_object(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
    except (UnicodeError, ValueError):
        raise LocalCliError(f'{label} did not return one valid JSON object') from None
    if not isinstance(value, dict):
        raise LocalCliError(f'{label} did not return one valid JSON object')
    return value


def _custom_json_array(raw: bytes, label: str) -> list:
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
    except (UnicodeError, ValueError):
        raise LocalCliError(f'{label} did not return one valid JSON array') from None
    if not isinstance(value, list):
        raise LocalCliError(f'{label} did not return one valid JSON array')
    return value


def _custom_line(text: str, label: str) -> str:
    lines = re.findall(rf'^  {re.escape(label)}: (.*)$', text, re.M)
    if len(lines) != 1 or not lines[0].strip():
        raise LocalCliError('custom-collaboration CLI line output is invalid')
    return lines[0].strip()


def _parse_trial_group(raw: bytes, driver_id: str, reviewer_id: str) -> tuple[str, str]:
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise LocalCliError('custom-collaboration trial Group output is invalid') from None
    if not text.startswith('Group created:\n'):
        raise LocalCliError('custom-collaboration trial Group output prefix is invalid')
    group_id = _custom_line(text, 'ID')
    session_id = _custom_line(text, 'Session')
    driver = _custom_line(text, 'Driver')
    participants = [value.strip() for value in _custom_line(text, 'Participants').split(',')]
    if (driver != driver_id or participants != [driver_id, reviewer_id]
            or not session_id.startswith(group_id + ':')):
        raise LocalCliError('custom-collaboration trial Group output differs from the owned expectation')
    try:
        return (identifier(group_id, 'custom-collaboration trial Group ID'),
                identifier(session_id, 'custom-collaboration trial Session ID'))
    except ValueError as error:
        raise LocalCliError(str(error)) from None


def _trial_participants(value: dict, expected_id: str, expected_role: str) -> list:
    participants = value.get('participants')
    if not isinstance(participants, list):
        raise LocalCliError('custom-collaboration trial membership output is invalid')
    rows = []
    for item in participants:
        if not isinstance(item, dict):
            raise LocalCliError('custom-collaboration trial membership output is invalid')
        rows.append((item.get('bot_uuid') or item.get('actor_id'), item.get('role'),
                     item.get('mode')))
    if not any(row[0] == expected_id and row[1] == expected_role for row in rows):
        raise LocalCliError('custom-collaboration trial membership differs from the owned expectation')
    return rows


def _trial_run(value: dict) -> dict:
    run = value.get('run')
    if not isinstance(run, dict):
        raise LocalCliError('custom-collaboration one-shot Run response is invalid')
    if (not isinstance(run.get('run_id'), str) or not run['run_id']
            or not isinstance(run.get('session_id'), str)
            or not isinstance(run.get('group_id'), str)
            or run.get('status') not in ('running', 'waiting')):
        raise LocalCliError('custom-collaboration one-shot Run identity differs from the owned expectation')
    return run


def _trial_graph(value: dict, run_id: str, session_id: str, group_id: str) -> bool:
    run = value.get('run')
    nodes = value.get('nodes')
    if (not isinstance(run, dict) or run.get('run_id') != run_id
            or run.get('session_id') != session_id or run.get('group_id') != group_id
            or not isinstance(nodes, list) or not nodes
            or any(not isinstance(node, dict) or not isinstance(node.get('node_id'), str)
                   for node in nodes)):
        raise LocalCliError('custom-collaboration execution graph differs from the owned expectation')
    if any(node.get('status') not in ('pending', 'running', 'completed', 'skipped', 'failed')
           for node in nodes):
        raise LocalCliError('custom-collaboration execution graph status differs')
    return True


def _request_trial_json(runtime: OwnedBcsProcess, method: str, path: str, body: dict | None,
                        bearer_token: str, expected_status: int) -> dict:
    origin = getattr(runtime.inputs, 'origin', None)
    if not isinstance(origin, str):
        raise LocalCliError('custom-collaboration trial requires a live owned runtime origin')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise LocalCliError('custom-collaboration trial requires the owned loopback origin')
    raw_body = None
    headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + bearer_token}
    if body is not None:
        headers['Content-Type'] = 'application/json'
        try:
            raw_body = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        except ValueError:
            raise LocalCliError('custom-collaboration trial request body is invalid') from None
    request = Request(origin + path, method=method, data=raw_body, headers=headers)
    try:
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                status, raw = response.status, response.read(65_537)
        except HTTPError as error:
            status, raw = error.code, error.read(65_537)
    except OSError:
        raise LocalCliError('custom-collaboration owned runtime request failed') from None
    if status != expected_status or len(raw) > 65_536 or bearer_token.encode() in raw:
        raise LocalCliError('custom-collaboration owned runtime outcome differs')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
    except (UnicodeError, ValueError):
        raise LocalCliError('custom-collaboration owned runtime returned invalid JSON') from None
    if not isinstance(value, dict):
        raise LocalCliError('custom-collaboration owned runtime returned invalid JSON')
    return value


def custom_collaboration_group_delete_path(group_id: str, driver_id: str) -> str:
    """Build the owned cleanup path for one exact custom-collaboration Group."""
    group_id = identifier(group_id, 'custom collaboration persistent Group ID')
    driver_id = identifier(driver_id, 'custom collaboration cleanup Bot ID')
    return (f'/groups/{quote(group_id, safe="")}'
            f'?bot_id={quote(driver_id, safe="")}')


TRIAL_TOPIC = 'CLI one-shot custom collaboration trial'
TRIAL_TOPIC = 'CLI one-shot custom collaboration trial'
TRIAL_CONTEXT = 'Release coordination through bcs-cli'
TRIAL_INPUT = '{"question":"Review the release workflow before persisting it."}'


def run_custom_collaboration_trial(runtime: OwnedBcsProcess, *, state_dir: Path,
                                   case_id: str, binary: Path, binary_sha256: str,
                                   yaml_path: Path, driver_id: str, reviewer_id: str,
                                   human_id: str, driver_token: str,
                                   timeout_seconds: int = 30) -> dict:
    """Execute the complete owned-local one-shot trial lifecycle.

    This remains one source-bound local slice. It never proves remote
    execution, parent-story closure, or release qualification.
    """
    if (not state_dir.is_absolute() or state_dir.is_symlink() or not state_dir.is_dir()):
        raise LocalCliError('product CLI requires an absolute regular state directory')
    identifier(case_id, 'CLI case ID')
    identifier(driver_id, 'custom collaboration driver Bot ID')
    identifier(reviewer_id, 'custom collaboration reviewer Bot ID')
    identifier(human_id, 'custom collaboration Human participant ID')
    identifier(driver_token, 'custom collaboration driver Bot credential')
    if driver_id == reviewer_id:
        raise LocalCliError('custom collaboration requires two distinct Bots')
    if not human_id.startswith('human_'):
        raise LocalCliError('custom collaboration requires a Human participant ID')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise LocalCliError('product CLI timeout must be 1..30 seconds')
    binding = custom_collaboration_source_binding()
    if binding['requirements'][2]['id'] != 'CUSTOM-COLLABORATION.trial-one-shot-lifecycle':
        raise LocalCliError('custom collaboration source binding changed')
    if not isinstance(yaml_path, Path):
        raise LocalCliError('collaboration YAML must be an absolute owned-state file path')
    if yaml_path.is_symlink():
        raise LocalCliError('collaboration YAML must not be a symlink')
    yaml_path = yaml_path.resolve()
    if (not yaml_path.is_file() or yaml_path.stat().st_size > 1_048_576
            or not yaml_path.is_relative_to(state_dir.resolve())):
        raise LocalCliError('collaboration YAML must be a finite regular file inside the owned Run state')
    state = read(state_dir / 'run.json')
    if (state.get('run_id') != runtime.run_id
            or not isinstance(state.get('ledger_path'), str)
            or Path(state['ledger_path']) != state_dir / 'run.sqlite'):
        raise LocalCliError('product CLI Run/ledger binding mismatch')
    require_cli_binary(binary, binary_sha256)
    binary = binary.resolve()
    root, context, environment = _private_context(runtime, state_dir)
    started = time.monotonic()
    trial_group_id = trial_session_id = run_id = ''
    graph_valid = False
    from .local_custom_collaboration_case import project_density
    density = {}

    def observe(method, data):
        density.update(project_density(method, data, group_id=trial_group_id,
                       session_id=trial_session_id, run_id=run_id,
                       driver_id=driver_id, reviewer_id=reviewer_id, human_id=human_id))
    try:
        write(context, {'token': driver_token, 'bot_uuid': driver_id,
                        'api_base_url': runtime.inputs.origin})
        group_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('create-group', '--driver', driver_id, '--participants', f'{driver_id},{reviewer_id}',
             '--context', TRIAL_CONTEXT, '--topic', TRIAL_TOPIC), timeout_seconds)
        if driver_token.encode() in group_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        trial_group_id, trial_session_id = _parse_trial_group(
            group_raw, driver_id, reviewer_id)
        observe("trial_create_group", {"id": trial_group_id, "session_id": trial_session_id})
        member_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('session', 'add-member', trial_session_id, '--bot-uuid', human_id,
             '--role', 'consultant'), timeout_seconds)
        if driver_token.encode() in member_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        member = _custom_json_object(member_raw, 'custom-collaboration trial membership')
        _trial_participants(member, human_id, 'consultant')
        member_id = member.get('session_id') or member.get('id')
        if member_id != trial_session_id:
            raise LocalCliError('custom-collaboration trial Session identity differs')

        observe("trial_add_member", member)
        permission_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('collaborate', 'permission', '--session', trial_session_id), timeout_seconds)
        if driver_token.encode() in permission_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        permission = _custom_json_object(permission_raw, 'custom-collaboration trial permission')
        if (permission.get('allowed') is not True
                or permission.get('session_id') != trial_session_id
                or permission.get('group_id') != trial_group_id
                or permission.get('caller_bot_id') != driver_id
                or permission.get('group_owner_bot_id') != driver_id
                or permission.get('reason_code') != 'allowed'
                or permission.get('policy_version') != 'session_state_machine_v1'
                or permission.get('group_strategy') != 'chat'):
            raise LocalCliError('custom-collaboration trial permission differs from the owned expectation')

        observe("trial_permission", permission)
        run_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('collaborate', 'run', str(yaml_path), '--session', trial_session_id,
             '--binding', f'planner={driver_id}', '--binding', f'reviewer={reviewer_id}',
             '--input', TRIAL_INPUT), timeout_seconds)
        if driver_token.encode() in run_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        run_value = _custom_json_object(run_raw, 'custom-collaboration one-shot Run')
        run = _trial_run(run_value)
        run_id = run['run_id']
        if run['session_id'] != trial_session_id or run['group_id'] != trial_group_id:
            raise LocalCliError('custom-collaboration Run ownership differs from the owned expectation')

        observe("trial_start_run", run_value)
        graph_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('collaborate', 'query', '--run', run_id, '--graph'), timeout_seconds)
        if driver_token.encode() in graph_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        graph = _custom_json_object(graph_raw, 'custom-collaboration execution graph')
        graph_valid = _trial_graph(graph, run_id, trial_session_id, trial_group_id)

        observe("trial_query_graph", graph)
        pending_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('collaborate', 'query', '--run', run_id, '--pending'), timeout_seconds)
        if driver_token.encode() in pending_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        if _custom_json_array(pending_raw, 'custom-collaboration pending Human inputs'):
            raise LocalCliError('Bot-only custom-collaboration Run must expose no pending Human input')

        observe("trial_query_pending", _custom_json_array(pending_raw, "pending Human inputs"))
        guard = capture([str(binary), '--json', '--log-level', 'error', 'collaborate',
                         'respond', '--run', run_id, '--node', 'plan',
                         '--content', 'invalid Human reply'],
                        cwd=root, environment=environment, timeout_seconds=timeout_seconds,
                        max_output_bytes=65_536, collect_stderr=True)
        if guard.returncode == 0:
            raise LocalCliError('custom-collaboration respond guard did not refuse')
        if driver_token.encode() in guard.stdout or driver_token.encode() in guard.stderr:
            raise LocalCliError('custom collaboration CLI emitted its credential')

        observe("trial_respond_guard", {"guard_refused": guard.returncode != 0,
                                       "not_pending": b"not pending" in guard.stderr})
        history_raw = _execute_custom_collaboration(
            binary, environment, root,
            ('session', 'messages', trial_session_id, '--view-bot', driver_id,
             '--limit', '50'), timeout_seconds)
        if driver_token.encode() in history_raw:
            raise LocalCliError('custom collaboration CLI emitted its credential')
        history = _custom_json_array(history_raw, 'custom-collaboration trial history')
        serialized_history = json.dumps(history, ensure_ascii=False)
        if ('bcsPanel.StateMachineRunView' not in serialized_history
                or run_id not in serialized_history):
            raise LocalCliError('custom-collaboration restored history differs from the owned expectation')

        observe("trial_messages", history)
        cancelled = _request_trial_json(runtime, 'POST',
                                        f'/state-machine-runs/{quote(run_id, safe="")}/cancel',
                                        {'reason': 'E2E one-shot trial completed'},
                                        driver_token, 200)
        cancel_run = cancelled.get('run')
        if (not isinstance(cancel_run, dict) or cancel_run.get('run_id') != run_id
                or cancel_run.get('status') not in ('aborted', 'completed', 'failed')):
            raise LocalCliError('custom-collaboration Run cancellation differs from the owned expectation')
        observe("cancel_trial_run", cancelled)
        session = _request_trial_json(runtime, 'GET',
                                     f'/sessions/{quote(trial_session_id, safe="")}', None,
                                     driver_token, 200)
        if ((session.get('session_id') or session.get('id')) != trial_session_id
                or session.get('group_id') != trial_group_id or session.get('status') != 'running'):
            raise LocalCliError('custom-collaboration trial Session preservation differs')
        observe("read_trial_session", session)
        deleted = _request_trial_json(
            runtime, 'DELETE',
            f'/groups/{quote(trial_group_id, safe="")}?bot_id={quote(driver_id, safe="")}',
            None, driver_token, 200)
        if deleted.get('deleted') is not True or deleted.get('id') != trial_group_id:
            raise LocalCliError('custom-collaboration trial Group cleanup differs')
        observe("delete_trial_group", deleted)
    except BaseException as operation_error:
        cleanup_unconfirmed = False
        try:
            if run_id:
                _request_trial_json(runtime, 'POST',
                                    f'/state-machine-runs/{quote(run_id, safe="")}/cancel',
                                    {'reason': 'E2E cleanup'}, driver_token, 200)
            if trial_group_id:
                _request_trial_json(
                    runtime, 'DELETE',
                    f'/groups/{quote(trial_group_id, safe="")}?bot_id={quote(driver_id, safe="")}',
                    None, driver_token, 200)
        except BaseException:
            cleanup_unconfirmed = True
        try:
            write(context, {'token': '', 'bot_uuid': '', 'api_base_url': ''})
            shutil.rmtree(root)
        except BaseException:
            cleanup_unconfirmed = True
        if cleanup_unconfirmed or root.exists():
            failure = LocalCliError('custom collaboration trial cleanup is unconfirmed')
            failure.credential_cleanup = 'UNCONFIRMED'
            failure.credential_context = str(root)
            raise failure from operation_error
        raise
    else:
        cleanup_unconfirmed = False
        try:
            write(context, {'token': '', 'bot_uuid': '', 'api_base_url': ''})
            shutil.rmtree(root)
        except BaseException:
            cleanup_unconfirmed = True
        if cleanup_unconfirmed or root.exists():
            failure = LocalCliError('custom collaboration context cleanup is unconfirmed')
            failure.credential_cleanup = 'UNCONFIRMED'
            failure.credential_context = str(root)
            raise failure
    return {'density_observations': density,
            'status': 'LOCAL_CUSTOM_COLLABORATION_TRIAL_PASS',
            'case_id': case_id,
            'parent_requirement_id': binding['requirements'][2]['id'],
            'operation': 'cli.collaboration.trial',
            'source': 'product-bcs-cli', 'authentication': 'OWNED_LOCAL_BOT',
            'binary_sha256': binary_sha256, 'yaml_path': str(yaml_path),
            'topic': TRIAL_TOPIC, 'context': TRIAL_CONTEXT,
            'group_id': trial_group_id, 'session_id': trial_session_id,
            'run_id': run_id, 'driver_bot': driver_id,
            'participants': sorted([driver_id, reviewer_id]), 'human_participant': human_id,
            'permission': 'ALLOWED', 'graph_valid': graph_valid,
            'pending_human_inputs': 0, 'respond_guard': 'REFUSED',
            'restored_history': True, 'run_status': 'TERMINAL',
            'session_preserved': True, 'group_cleanup': 'DELETED',
            'context_cleanup': 'REMOVED',
            'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
            'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            'full_parent_coverage': 'NOT_RUN'}

def validate_collaboration_yaml(runtime: OwnedBcsProcess, *, state_dir: Path,
                                case_id: str, binary: Path, binary_sha256: str,
                                yaml_path: Path, timeout_seconds: int = 10) -> dict:
    """Execute one read-only product-CLI YAML validation for the owned Run.

    This is an explicit one-requirement slice, never proof that the complete
    custom-collaboration parent Story was migrated or closed remotely.
    """
    if (not state_dir.is_absolute() or state_dir.is_symlink() or not state_dir.is_dir()):
        raise LocalCliError('product CLI requires an absolute regular state directory')
    identifier(case_id, 'CLI case ID')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise LocalCliError('product CLI timeout must be 1..30 seconds')
    binding = custom_collaboration_source_binding()
    require_cli_binary(binary, binary_sha256)
    binary = binary.resolve()
    if not isinstance(yaml_path, Path):
        raise LocalCliError('collaboration YAML must be an absolute owned-state file path')
    if yaml_path.is_symlink():
        raise LocalCliError('collaboration YAML must not be a symlink')
    yaml_path = yaml_path.resolve()
    if (not yaml_path.is_file() or yaml_path.stat().st_size > 1_048_576
            or not yaml_path.is_relative_to(state_dir.resolve())):
        raise LocalCliError('collaboration YAML must be a finite regular file inside the owned Run state')
    state = read(state_dir / 'run.json')
    if (state.get('run_id') != runtime.run_id
            or not isinstance(state.get('ledger_path'), str)
            or Path(state['ledger_path']) != state_dir / 'run.sqlite'):
        raise LocalCliError('product CLI Run/ledger binding mismatch')
    root, _context_file, environment = _private_context(runtime, state_dir)
    started = time.monotonic()
    try:
        result = capture([str(binary), '--json', '--log-level', 'error',
                          'collaboration', 'validate', str(yaml_path)],
                         cwd=root, environment=environment, timeout_seconds=timeout_seconds,
                         max_output_bytes=65_536)
    except ProcessCaptureError as error:
        public_error = LocalCliError(str(error))
        if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
            public_error.child_shutdown_status = 'UNCONFIRMED'
        raise public_error from None
    if result.returncode != 0:
        raise LocalCliError('product CLI returned a nonzero exit status')
    try:
        value = json.loads(result.stdout.decode('utf-8'), object_pairs_hook=unique_keys)
    except (UnicodeError, ValueError):
        raise LocalCliError('product CLI did not return one valid JSON object') from None
    if not isinstance(value, dict):
        raise LocalCliError('product CLI did not return one valid JSON object')
    summary = value.get('summary')
    if (value.get('valid') is not True or not isinstance(summary, dict)
            or summary.get('participants') != 2 or summary.get('nodes') != 2):
        raise LocalCliError('product CLI collaboration summary differs from the owned expectation')
    shutil.rmtree(root)
    from .local_custom_collaboration_case import project_density
    return {'density_observations': project_density("validate_yaml", value),
            'status': 'LOCAL_CUSTOM_COLLABORATION_VALIDATE_PASS',
            'case_id': case_id,
            'parent_requirement_id': binding['requirements'][0]['id'],
            'operation': 'cli.collaboration.validate',
            'source': 'product-bcs-cli', 'authentication': 'NONE',
            'binary_sha256': binary_sha256, 'yaml_path': str(yaml_path),
            'valid': True, 'summary': {'participants': 2, 'nodes': 2},
            'context_cleanup': 'REMOVED',
            'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
            'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            'full_parent_coverage': 'NOT_RUN'}
