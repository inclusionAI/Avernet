"""One owned runtime for the seven reviewed Edge friend-mutation parents plus
the two backlog TestCase slices in `edge_permission.sh`.

The backlog TC friend-auth-sync obligation needs a real outbound callback, so
the run also owns one loopback Provider/Judge mock child and binds it as
`friend_work_order_base_url` before the BCS process starts. This is a local
test child only; no OpenClaw or engine-adapter process is involved.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from .case_executor import execute_case
from .edge_friend_mutation_case import (
    BACKLOG_CASE_IDS,
    CASE_IDS,
    RELEASE_CASE_IDS,
    RELEASE_PARENT_IDS,
    edge_friend_mutation_case_plan,
    edge_friend_mutation_source_binding,
)
from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_edge_friend_mutations_case import (
    OwnedEdgeFriendFixtureAdmission,
    OwnedEdgeFriendMutationsAdapter,
)
from .local_fixtures import OwnedLocalFixtures
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write

SUITE = 'owned-local-edge-friend-mutations-v1'
ROLE_MUTATIONS = ('CEO', 'PM', 'ENG', 'QA')
MOCK_SCRIPT = Path(__file__).resolve().parents[2] / 'e2e-test' / 'http_provider_judge_mock.py'
MOCK_SCRIPT_NAME = 'http_provider_judge_mock.py'
MOCK_DEADLINE_SECONDS = 60.0
MOCK_STOP_TIMEOUT = 10.0
MOCK_TARGET_PREFIX = 'edge-friend-auth-mock-'
MOCK_MAX_STDOUT_BYTES = 1024 * 1024
MOCK_MAX_STDERR_BYTES = 1024 * 1024
MOCK_READY_ERROR = 'edge-friend-auth-mock child failed before readiness'
MOCK_STOP_ERROR = 'edge-friend-auth-mock child stop outcome is unknown or failed'
MOCK_OUT_OF_BOUNDS_ERROR = 'edge-friend-auth-mock child exceeded its bounded evidence budget'
_NORMAL_STOP_RETURN_CODES = (0, -signal.SIGTERM, 128 + signal.SIGTERM)


def journal_filename(case_id: str) -> str:
    return 'local-edge-friend-mutations-' + case_id + '.json'


def _validate_mock_origin(origin: str) -> str:
    try:
        parsed = urlsplit(origin)
        parsed_port = parsed.port
    except (TypeError, ValueError):
        raise RunStateError(MOCK_READY_ERROR) from None
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed_port is None or not 1024 <= parsed_port <= 65535
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise RunStateError(MOCK_READY_ERROR)
    return origin


class OwnedFriendAuthSyncMock:
    """Own one loopback Provider/Judge HTTP child for the backlog TestCase."""

    def __init__(self, run_id: str, parent: Path):
        identifier(run_id, 'Edge friend-mutation Run ID')
        if parent.is_symlink() or not parent.is_dir():
            raise RunStateError('Edge friend mock requires an existing non-symlink parent')
        self.run_id = run_id
        self._root = Path(tempfile.mkdtemp(prefix=MOCK_TARGET_PREFIX, dir=parent))
        self._ready_path = self._root / 'ready.txt'
        self._stdout_path = self._root / 'stdout.log'
        self._stderr_path = self._root / 'stderr.log'
        self._stdout = None
        self._stderr = None
        self.process: subprocess.Popen | None = None
        self.origin: str | None = None
        self.state = 'NEW'
        self.record: dict | None = None

    def start(self) -> 'OwnedFriendAuthSyncMock':
        if self.state != 'NEW' or self.process is not None:
            raise RunStateError(MOCK_READY_ERROR)
        if (MOCK_SCRIPT.is_symlink() or not MOCK_SCRIPT.is_file()
                or MOCK_SCRIPT.stat().st_size > 1_048_576):
            raise RunStateError(MOCK_READY_ERROR)
        def open_stream(path: Path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            return os.fdopen(fd, 'wb')
        try:
            self._stdout = open_stream(self._stdout_path)
            self._stderr = open_stream(self._stderr_path)
            self.process = subprocess.Popen(
                [sys.executable, str(MOCK_SCRIPT), '--host', '127.0.0.1',
                 '--port', '0', '--ready-file', str(self._ready_path)],
                cwd=str(self._root), env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
                stdin=subprocess.DEVNULL, stdout=self._stdout, stderr=self._stderr,
                start_new_session=True)
        except BaseException as error:
            self._close_streams()
            if self.process is not None:
                self._terminate()
            raise RunStateError(MOCK_READY_ERROR) from error
        self.state = 'STARTING'
        self._await_ready()
        self.state = 'RUNNING'
        return self

    def _await_ready(self) -> None:
        deadline = time.monotonic() + MOCK_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._close_streams()
                raise RunStateError(MOCK_READY_ERROR)
            if self._ready_path.is_file():
                try:
                    origin = self._ready_path.read_text(encoding='utf-8').strip()
                except BaseException as error:
                    self._close_streams()
                    raise RunStateError(MOCK_READY_ERROR) from error
                try:
                    self.origin = _validate_mock_origin(origin)
                except RunStateError:
                    self._close_streams()
                    raise
                return
            time.sleep(0.1)
        try:
            self.stop()
        except BaseException:
            pass
        raise RunStateError(MOCK_READY_ERROR)

    def _close_streams(self) -> None:
        for stream in (self._stdout, self._stderr):
            if stream is not None:
                try:
                    stream.close()
                finally:
                    pass
        self._stdout = None
        self._stderr = None

    def _terminate(self) -> None:
        """Terminate only the process group created by this object."""
        assert self.process is not None
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=MOCK_STOP_TIMEOUT)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=MOCK_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RunStateError(MOCK_STOP_ERROR) from None

    def stop(self) -> dict:
        if self.record is not None:
            return dict(self.record)
        if self.process is None:
            raise RunStateError(MOCK_STOP_ERROR)
        self._terminate()
        self._close_streams()
        stdout_size = self._stdout_path.stat().st_size
        stderr_size = self._stderr_path.stat().st_size
        if stdout_size > MOCK_MAX_STDOUT_BYTES or stderr_size > MOCK_MAX_STDERR_BYTES:
            self.state = 'FAILED_STOP'
            raise RunStateError(MOCK_OUT_OF_BOUNDS_ERROR)
        returncode = self.process.returncode
        stopped = returncode in _NORMAL_STOP_RETURN_CODES
        self.state = 'STOPPED' if stopped else 'FAILED_STOP'
        self.record = {
            'schema_version': 1,
            'run_id': self.run_id,
            'script': MOCK_SCRIPT_NAME,
            'status': self.state,
            'returncode': returncode,
            'origin': self.origin,
            'stdout_bytes': stdout_size,
            'stderr_bytes': stderr_size,
            'ready': True,
            'stop_signal_accepted': stopped,
            'credentials_in_evidence': False,
            'engine_process': 'NOT_STARTED',
        }
        if not stopped:
            raise RunStateError(MOCK_STOP_ERROR)
        return dict(self.record)

    def discard_root(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RunStateError(MOCK_STOP_ERROR)
        shutil.rmtree(self._root, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        try:
            if self.process is not None:
                self.stop()
        finally:
            self.discard_root()


def local_edge_friend_mutations_run(*, binary: Path, binary_sha256: str,
                                    state_dir: Path, run_id: str,
                                    confirm_run_id: str,
                                    allow_local_processes: bool,
                                    allow_fixture_write: bool,
                                    allow_write: bool,
                                    case_ids: tuple[str, ...] = CASE_IDS) -> dict:
    """Execute the selected Edge friend obligations in one owned runtime.

    ``case_ids`` defaults to the complete nine-Case suite and accepts only a
    non-empty ordered subsequence of that frozen plan.  The two red Edge
    TestCase migrations inherit this runtime, mock child and fixture lifecycle
    without duplicating transport code or receipt assertions.
    """
    if (not case_ids or any(value not in CASE_IDS for value in case_ids)
            or len(case_ids) != len(set(case_ids))
            or list(case_ids) != [value for value in CASE_IDS if value in set(case_ids)]):
        raise RunStateError('Edge friend-mutation selection must be a non-empty ordered subsequence')
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Edge friend mutation requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Edge friend mutation Run ID')
    state_dir = Path(state_dir)
    if state_dir.is_symlink() or state_dir.exists():
        raise RunStateError('Edge friend mutation requires a fresh state directory; existing evidence is retained')
    prepared_directory = state_dir.parent
    try:
        prepared_directory.mkdir(parents=True, exist_ok=True)
    except BaseException as error:
        raise RunStateError('Edge friend mutation state directory parent is not writable') from error
    if prepared_directory.is_symlink() or not prepared_directory.is_dir():
        raise RunStateError('Edge friend mutation state directory parent cannot be a symlink')
    source = executor_digest()
    bindings = {case_id: edge_friend_mutation_source_binding(case_id)
                for case_id in case_ids}
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if set(RELEASE_PARENT_IDS) - {row['id'] for row in reviewed}:
        raise RunStateError('Edge friend-mutation reviewed parents are missing from the reviewed release denominator')

    runtime = identities = None
    cases: list[dict] = []
    error: BaseException | None = None
    mock = OwnedFriendAuthSyncMock(run_id, prepared_directory)
    mock_record: dict | None = None
    previous = signal.signal(signal.SIGTERM,
                             lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            mock.start()
            binding = {
                'schema_version': 1, 'contract': SUITE, 'suite': SUITE,
                'run_id': run_id, 'case_ids': list(case_ids),
                'release_parent_case_ids': list(RELEASE_CASE_IDS),
                'backlog_case_ids': list(BACKLOG_CASE_IDS),
                'expected_assertion_count': sum(len(bindings[value]['requirements'])
                                                for value in case_ids),
                'executor_digest': source, 'bcs_binary_sha256': binary_sha256,
                'selected_requirement_ids': {
                    case_id: [row['id'] for row in bindings[case_id]['requirements']]
                    for case_id in case_ids},
                'source_bindings': bindings, 'legacy_inventory': legacy,
                'runtime_dir': str(state_dir.resolve()),
                'friend_work_order_base_url': mock.origin,
                'mock_child': {
                    'script': MOCK_SCRIPT_NAME,
                    'ready_protocol': 'ready-file',
                    'process_owner': 'runner-owned-child',
                    'engine_process': 'NOT_STARTED',
                },
            }
            try:
                with OwnedBcsProcess(
                        binary=binary, binary_sha256=binary_sha256,
                        root=state_dir, run_id=run_id,
                        allow_ambient_local_identity=False,
                        friend_work_order_base_url=mock.origin) as owned_runtime:
                    runtime = owned_runtime
                    write(runtime.root / 'edge-friend-mutations-binding.json', binding)
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=ROLE_MUTATIONS) as owned_identities:
                        identities = owned_identities
                        fixtures.apply(allow_write=True, role_identity=identities)
                        for role in ROLE_MUTATIONS:
                            identities.verify(role)
                        fixture_admission = OwnedEdgeFriendFixtureAdmission(
                            runtime, bot_id=identities.actor_id('CEO'))
                        for case_id in case_ids:
                            plan = edge_friend_mutation_case_plan(case_id)
                            adapter = OwnedEdgeFriendMutationsAdapter(
                                runtime, identities, fixture_admission,
                                case_id=case_id, allow_write=allow_write)
                            cases.append(execute_case(plan, adapter).snapshot())
            except BaseException as runtime_error:
                error = runtime_error
        finally:
            try:
                if mock.process is not None:
                    mock_record = mock.stop()
                    if runtime is not None and runtime.root.is_dir():
                        write(runtime.root / 'edge-friend-mock.json', mock_record)
            except BaseException as stop_error:
                if error is None:
                    error = stop_error
            finally:
                mock.discard_root()
    except BaseException as caught:
        if error is None:
            error = caught
    finally:
        signal.signal(signal.SIGTERM, previous)

    if runtime is None or not runtime.root.is_dir():
        if error is not None:
            raise error
        raise RunStateError('Edge friend-mutation owned runtime was not created')
    root = runtime.root
    binding_path = root / 'edge-friend-mutations-binding.json'
    binding_record = read(binding_path) if binding_path.is_file() else {}
    expected_journals = {
        case_id: read(root / journal_filename(case_id))
        for case_id in case_ids
        if (root / journal_filename(case_id)).is_file()
    }
    runtime_record = runtime._record()
    credentials = (identities.summary() if identities is not None
                   else {'credential_memory': 'NOT_CAPTURED'})
    passed = (
        error is None and executor_digest() == source
        and binding_record == binding
        and mock_record is not None
        and mock_record.get('status') == 'STOPPED'
        and len(cases) == len(case_ids)
        and set(expected_journals) == set(case_ids)
        and all(cases[index].get('case_id') == case_id
                 and expected_journals[case_id].get('status') == 'PASS'
                 and cases[index].get('status') == 'PASS'
                 and cases[index].get('error_type') is None
                 and cases[index].get('completed_count') == cases[index].get('total_count')
                 for index, case_id in enumerate(case_ids))
        and all(step.get('status') == 'PASS' for journal in expected_journals.values()
                for step in journal.get('steps', []))
        and runtime_record.get('status') == 'STOPPED'
        and runtime_record.get('returncode') == 0
        and credentials.get('credential_memory') == 'CLEARED'
    )
    status = ('CANCELLED' if error is not None and has_cancellation(error)
              else 'LOCAL_EDGE_FRIEND_MUTATIONS_PASS' if passed
              else 'BLOCKED')
    result = {
        'schema_version': 1, 'status': status, 'run_id': run_id,
        'suite': SUITE, 'case_ids': list(case_ids),
        'release_parent_case_ids': list(RELEASE_CASE_IDS),
        'backlog_case_ids': list(BACKLOG_CASE_IDS), 'cases': cases,
        'executor_digest': source, 'shutdown': runtime_record,
        'mock_child': mock_record, 'credentials': credentials,
        'remote_validation': 'NOT_RUN',
        'full_release_status': 'NOT_QUALIFIED', 'ready_to_execute': False,
    }
    if error is not None:
        result['error_type'] = type(error).__name__
    write(root / 'edge-friend-mutations-flow-report.json', result)
    if mock_record is not None and set(expected_journals) == set(case_ids) == set(CASE_IDS):
        try:
            from .local_edge_friend_mutations_reporting import (
                publish_edge_friend_mutations,
            )
            canonical = publish_edge_friend_mutations(runtime, binding, result)
            result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
            if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
                result['status'] = 'BLOCKED'
        except BaseException as publication_error:
            result['status'] = 'BLOCKED'
            result['publication_error_type'] = type(publication_error).__name__
    return result
