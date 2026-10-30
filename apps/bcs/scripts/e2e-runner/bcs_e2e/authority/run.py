"""Owned-local dual-leg execution for the Bot-authority journey slice.

One frozen 42-obligation child story runs beside one owned BCS server
binary (sheltered wpe3 artifact, sha256 re-verified on entry). The owned
runtime starts under the strict admission configuration (ambient local
identity off, mock headers admitted on the registration plane only), the
child replays the journey over raw product HTTP with freshly minted
Gateway Principal JWTs, and the runner publishes the owned-local receipt
only after the permission, marker, redaction, obligation, drift and
binary-digest fences all pass. The product CLI binary owns exactly one
readiness gate subprocess; it never touches a business path. Nothing
credential-bearing is ever written to argv or to any evidence file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ..errors import has_cancellation
from ..inventory import (assert_reviewed_parent, current_manifest, executor_digest,
                         reviewed_release_inventory)
from ..owned_bcs import OwnedBcsProcess, file_sha256
from ..profile import identifier
from ..run_state import RunStateError, read, write
from .checks import scan_redaction, verify_all_obligations
from .contract import (BUSINESS_HTTP_CALLS, BCS_BINARY_SHA256, BOOTSTRAP_HTTP_CALLS, CASE_ID,
                       CLI_BINARY_SHA256, CLI_PROVENANCE, DISPOSITION, ENVIRONMENT,
                       HTTP_CALL_COUNT, MARKER_PATTERNS, MINE_USER_ID, OWNED_PROVENANCE,
                       PARENT, PRINCIPAL_SIGNING_KEY_ENV, REQUIREMENTS, STATUS, STORY_CHECKS,
                       SUITE, TARGET_USER_ID, authority_source_binding)
from .reporting import (BINDING_NAME, CASE_NAME, FLOW_NAME, build_authority_summary,
                        publish_authority_report)

BINARY_KIND = 'bcs'
STORY_SOURCE = Path(__file__).with_name('story.py')
STORY_DEADLINE_SECONDS = 900
BOUNDED_STREAM_BYTES = 8 * 1024 * 1024
EVIDENCE_NAME = 'authority-evidence.json'
STORY_LOG_NAME = 'authority-story.log'
STORY_ERR_LOG_NAME = 'authority-story.err.log'


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_authority_binary(binary: Path, binary_sha256: str, *, label: str = 'BCS') -> Path:
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError(f'authority {label} binary must be an executable regular file')
    if not isinstance(binary_sha256, str) or len(binary_sha256) != 64:
        raise RunStateError(f'authority {label} binary digest is invalid')
    if file_sha256(binary) != binary_sha256:
        raise RunStateError(f'authority {label} binary digest differs from its frozen declaration')
    return binary


def _parse_story_marker(text) -> tuple[bool, int]:
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='replace')
    match = MARKER_PATTERNS[0].search(text)
    if match is None:
        return False, 0
    return True, int(match.group(1))


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass


def _launch_story(argv: list[str], cwd: Path, deadline: float,
                  env: dict[str, str] | None = None) -> dict:
    """Test seam: one bounded black-box child in its own process group."""
    process = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, env=env)
    started = time.monotonic()
    try:
        out, err = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise RunStateError('authority story exceeded its 900-second deadline') from None
    except BaseException:
        _terminate_process_group(process)
        raise
    return {'exit_code': process.returncode, 'timed_out': False,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': out, 'stderr': err, 'process': process}


def _bounded_write(path: Path, data: bytes) -> None:
    if len(data) > BOUNDED_STREAM_BYTES:
        raise RunStateError('authority child stream exceeded its bounded evidence limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _read_optional(path: Path) -> bytes:
    if not path.exists() or path.is_symlink():
        return b''
    return path.read_bytes()


def _redaction_entries(stream_items: list[tuple[str, bytes]],
                       artifact_items: list[tuple[str, bytes]]) -> tuple[list[dict], list[dict], bool]:
    streams = [scan_redaction(name, payload) for name, payload in stream_items]
    artifacts = [scan_redaction(name, payload) for name, payload in artifact_items]
    return streams, artifacts, all(row['clean'] is True for row in streams + artifacts)


def local_authority_dual_run(*, binary: Path, binary_sha256: str,
                             cli_binary: Path, cli_binary_sha256: str, state_dir: Path,
                             run_id: str, confirm_run_id: str,
                             allow_local_processes: bool, allow_fixture_write: bool,
                             allow_write: bool) -> dict:
    """Run the frozen Bot-authority journey once beside an owned BCS child."""
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Bot authority journey requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Bot authority Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Bot authority journey requires a fresh state directory; existing evidence is retained')
    parent = state_dir.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise RunStateError('Bot authority journey requires a non-symlink directory parent for its state')
    binary = validate_authority_binary(Path(binary), binary_sha256, label='BCS')
    cli_binary = validate_authority_binary(Path(cli_binary), cli_binary_sha256, label='CLI')
    source = executor_digest()
    original = authority_source_binding()
    legacy = current_manifest()['legacy_inventory']
    if not reviewed_release_inventory() or PARENT not in {row['id'] for row in reviewed_release_inventory()}:
        raise RunStateError('Bot authority parent is missing from the reviewed release denominator')
    assert_reviewed_parent(PARENT, family='authority-dual')
    binding = {
        'schema_version': 1,
        'contract': SUITE,
        'suite': SUITE,
        'case_id': CASE_ID,
        'run_id': run_id,
        'executor_digest': source,
        'binary_kind': BINARY_KIND,
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': cli_binary_sha256,
        'auto_build': False,
        'strict_auth': True,
        'source_binding': original,
        'selected_requirement_ids': list(REQUIREMENTS),
        'legacy_inventory': legacy,
        'environment': dict(ENVIRONMENT),
        'runtime_dir': str(state_dir.resolve()),
        'binary_provenance': {'bcs': OWNED_PROVENANCE, 'bcs-cli': CLI_PROVENANCE},
        'frozen_binaries': {'bcs_sha256': BCS_BINARY_SHA256, 'bcs_cli_sha256': CLI_BINARY_SHA256},
    }
    error = None
    root = None
    startup_record = None
    story = None
    story_argv = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id,
                                 allow_ambient_local_identity=False) as owned_runtime:
                root = owned_runtime.root
                binding['human_id'] = owned_runtime.inputs.human_id
                write(root / BINDING_NAME, binding)
                startup_record = read(root / 'owned-bcs.json')
                if startup_record.get('status') != 'RUNNING' or startup_record.get('ready') is not True:
                    raise RunStateError('Bot authority owned runtime did not report a ready runtime')
                strict_configuration = json.loads(
                    (root / 'bcs-config.json').read_text(encoding='utf-8')).get('auth')
                if strict_configuration != {'chain': ['local', 'session'],
                                            'require_authentication': False,
                                            'allow_mock_headers': True}:
                    raise RunStateError('Bot authority owned runtime lost its strict admission configuration')
                evidence_path = root / EVIDENCE_NAME
                story_argv = [sys.executable, str(STORY_SOURCE),
                              '--base-url', owned_runtime.inputs.origin,
                              '--owner-human', owned_runtime.inputs.human_id,
                              '--target-user-id', TARGET_USER_ID,
                              '--mine-user-id', MINE_USER_ID,
                              '--evidence-file', str(evidence_path),
                              '--cli-binary', str(cli_binary),
                              '--principal-signing-key-env', PRINCIPAL_SIGNING_KEY_ENV,
                              '--run-tag', run_id]
                signing_key_value = owned_runtime.inputs.environment.get(PRINCIPAL_SIGNING_KEY_ENV, '')
                if signing_key_value and any(signing_key_value in str(element)
                                             for element in story_argv):
                    raise RunStateError('authority child argv leaked the principal signing key value')
                story = _launch_story(story_argv, cwd=root, deadline=STORY_DEADLINE_SECONDS,
                                      env=dict(os.environ, **owned_runtime.inputs.environment))
        except BaseException as failure:
            error = failure
        if root is None:
            if error is not None:
                raise error
            raise RunStateError('Bot authority owned runtime was not created')
        owned_record = read(root / 'owned-bcs.json')
        story_stdout = story['stdout'] if story is not None else b''
        story_stderr = story['stderr'] if story is not None else b''
        console = _read_optional(root / 'console.log')
        evidence_raw = _read_optional(root / EVIDENCE_NAME)
        marker_ok, observed_checks = (False, 0)
        if story is not None:
            marker_ok, observed_checks = _parse_story_marker(
                story_stdout.decode('utf-8', errors='replace'))
        evidence = None
        try:
            evidence = json.loads(evidence_raw) if evidence_raw else None
        except (UnicodeError, json.JSONDecodeError):
            evidence = None
        child_exit_ok = (story is not None and story['exit_code'] == 0
                         and story['timed_out'] is False and marker_ok
                         and observed_checks == STORY_CHECKS)
        provisional_case = {}
        if child_exit_ok and isinstance(evidence, dict):
            provisional_case = {
                'schema_version': 1, 'status': STATUS, 'run_id': run_id,
                'case_id': CASE_ID, 'executor_digest': source,
                'binary_kind': BINARY_KIND, 'bcs_binary_sha256': binary_sha256,
                'cli_binary_sha256': cli_binary_sha256, 'auto_build': False,
                'strict_auth': True,
                'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
                'story_lines': {row['id']: list(row['story_lines'])
                                for row in original['requirements']},
                'story_markers': {'observed': marker_ok, 'live_http_assertions': observed_checks},
                'resource_disposition': DISPOSITION,
                'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
            }
        child_receipt = {
            'schema_version': 1, 'run_id': run_id, 'case_id': CASE_ID,
            'argv': [str(item) for item in (story_argv or [])],
            'exit_code': story['exit_code'] if story is not None else None,
            'timed_out': story['timed_out'] if story is not None else True,
            'duration_seconds': story['duration_seconds'] if story is not None else 0.0,
            'markers': {'authority-journey-compliance': marker_ok},
            'observed_checks': observed_checks,
            'stdout_sha256': _sha256(story_stdout), 'stdout_bytes': len(story_stdout),
            'stderr_sha256': _sha256(story_stderr), 'stderr_bytes': len(story_stderr),
            'identity_mode': 'register',
        }
        provisional_result = {
            'schema_version': 1, 'status': STATUS if child_exit_ok else 'BLOCKED',
            'run_id': run_id, 'case_id': CASE_ID, 'suite': SUITE,
            'executor_digest': source, 'binary_kind': BINARY_KIND,
            'bcs_binary_sha256': binary_sha256, 'cli_binary_sha256': cli_binary_sha256,
            'auto_build': False, 'strict_auth': True,
            'child_receipt': child_receipt, 'case': provisional_case,
            'checks': {'status': 'PASS' if child_exit_ok else 'BLOCKED', 'checked': []},
            'redaction': {'status': 'PASS' if story is not None else 'BLOCKED'},
            'runtime_shutdown': {'status': owned_record.get('status'),
                                 'returncode': owned_record.get('returncode'),
                                 'ready': owned_record.get('ready')},
            'credentials': {'credential_memory': 'NOT_CAPTURED',
                            'story_key_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY'},
            'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False, 'resource_disposition': DISPOSITION,
            'http_call_count': (evidence.get('http_call_count')
                                if isinstance(evidence, dict) else None),
        }
        candidate_summary = build_authority_summary(binding, provisional_result)
        stream_items = [('story.stdout', story_stdout), ('story.stderr', story_stderr),
                        ('owned.console', console), ('child.evidence', evidence_raw)]
        artifact_items = [('child.receipt', json.dumps(child_receipt, sort_keys=True).encode()),
                          ('flow.report', json.dumps(provisional_result, sort_keys=True).encode()),
                          ('canonical.report', json.dumps(candidate_summary, sort_keys=True).encode())]
        streams, artifacts, redaction_ok = _redaction_entries(stream_items, artifact_items)
        redaction = {'token_material_captured': False, 'streams': streams,
                     'artifacts': artifacts, 'status': 'PASS' if redaction_ok else 'BLOCKED'}
        checks = {'status': 'BLOCKED', 'checked': []}
        if child_exit_ok and isinstance(evidence, dict):
            checked_evidence = dict(evidence)
            checked_evidence['redaction'] = redaction
            try:
                verified = verify_all_obligations(checked_evidence)
                checks = {'status': verified['status'], 'checked': verified['checked']}
            except RunStateError as failure:
                checks = {'status': 'BLOCKED', 'checked': [],
                          'error_type': type(failure).__name__}
        http_matrix_ok = (isinstance(evidence, dict)
                          and evidence.get('http_call_count') == HTTP_CALL_COUNT
                          and evidence.get('bootstrap_http_call_count') == BOOTSTRAP_HTTP_CALLS
                          and evidence.get('business_http_call_count') == BUSINESS_HTTP_CALLS)
        child_pass = (error is None and child_exit_ok and isinstance(evidence, dict)
                      and redaction_ok and checks.get('status') == 'PASS'
                      and startup_record is not None
                      and startup_record.get('status') == 'RUNNING'
                      and startup_record.get('ready') is True
                      and owned_record.get('status') == 'STOPPED'
                      and owned_record.get('returncode') == 0
                      and http_matrix_ok
                      and evidence.get('case_id') == CASE_ID
                      and executor_digest() == source
                      and read(root / BINDING_NAME) == binding
                      and file_sha256(binary) == binary_sha256
                      and file_sha256(cli_binary) == cli_binary_sha256)
        case = dict(provisional_case) if child_pass else {}
        if child_pass:
            case['checks'] = checks
            case['redaction'] = redaction
        result = dict(provisional_result)
        result.update({
            'status': STATUS if child_pass else ('CANCELLED'
                                                 if error is not None and has_cancellation(error)
                                                 else 'BLOCKED'),
            'child_receipt': child_receipt, 'case': case, 'checks': checks,
            'redaction': redaction,
            'runtime_shutdown': {'status': owned_record.get('status'),
                                 'returncode': owned_record.get('returncode'),
                                 'ready': owned_record.get('ready')},
        })
        if not redaction_ok:
            result['error_type'] = 'RedactionGateError'
        elif error is not None:
            result['error_type'] = type(error).__name__
        if redaction_ok and story is not None:
            _bounded_write(root / STORY_LOG_NAME, story_stdout)
            _bounded_write(root / STORY_ERR_LOG_NAME, story_stderr)
        if child_pass:
            write(root / CASE_NAME, case)
        write(root / FLOW_NAME, result)
        binding_unchanged = read(root / BINDING_NAME) == binding
        artifact_unchanged = (executor_digest() == source
                              and file_sha256(binary) == binary_sha256
                              and file_sha256(cli_binary) == cli_binary_sha256)
        if not (binding_unchanged and artifact_unchanged):
            result['status'] = 'BLOCKED'
            result['error_type'] = 'DriftFenceError'
            write(root / FLOW_NAME, result)
            result['canonical_summary'] = build_authority_summary(binding, result)
            result['outputs'] = {}
            return result
        canonical = publish_authority_report(root, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] != 'LOCAL_SUITE_PASS':
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
        story_process = story.get('process') if isinstance(story, dict) else None
        if story_process is not None and story_process.poll() is None:
            _terminate_process_group(story_process)
