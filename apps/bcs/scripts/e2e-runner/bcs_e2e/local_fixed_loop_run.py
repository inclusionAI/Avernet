"""One explicit owned-local lifecycle for the complete fixed-Loop Story."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import signal

from .case_executor import execute_fixed_loop_case
from .case_rewrite import fixed_loop_case_rewrite
from .errors import has_cancellation
from .inventory import current_manifest, executor_digest, reviewed_release_inventory
from .local_fixtures import OwnedLocalFixtures
from .local_fixed_loop_case import OwnedFixedLoopCaseAdapter
from .local_fixed_loop_contract import (ASSERTION_IDS, REQUIRED_ASSERTION_COUNT, CASE_ID,
                                       PARENT, SUITE, fixed_loop_source_binding)
from .local_judge_sandbox import OwnedLocalJudgeSandbox
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import identifier
from .run_state import RunStateError, read, write

# One deterministic scripted editorial sequence: the approved Run
# consumes the first two admissions (revise, approved) and the
# exhausted Run consumes the remaining two (revise, revise).
_JUDGE_OUTCOMES = ('revise', 'approved', 'revise', 'revise')
_PROVIDER_RECEIPT_LIMIT = 8


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_fixed_loop_binary(binary: Path, binary_sha256: str) -> Path:
    """Freeze the owned BCS server binary before launching anything."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError('Fixed Loop BCS binary must be a regular frozen file')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError('Fixed Loop BCS binary must be executable')
    if _sha256(binary.read_bytes()) != binary_sha256:
        raise RunStateError('Fixed Loop BCS binary digest differs from its frozen declaration')
    return binary


def local_fixed_loop_run(*, binary: Path, binary_sha256: str, state_dir: Path, run_id: str,
                          confirm_run_id: str, allow_local_processes: bool,
                          allow_fixture_write: bool, allow_write: bool) -> dict:
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('Fixed Loop requires process/fixture/business permissions and exact Run confirmation')
    identifier(run_id, 'Fixed Loop Run ID')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('Fixed Loop requires a fresh state directory; existing evidence is retained')
    source = executor_digest()
    original = fixed_loop_source_binding()
    plan = fixed_loop_case_rewrite()
    legacy = current_manifest()['legacy_inventory']
    reviewed = reviewed_release_inventory()
    if PARENT not in {row['id'] for row in reviewed}:
        raise RunStateError('Fixed Loop parent is missing from the original release denominator')
    binding = {
        'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
        'run_id': run_id, 'executor_digest': source, 'bcs_binary_sha256': binary_sha256,
        'store_messages': True, 'loop_execution_enabled': True,
        'judge_outcomes': list(_JUDGE_OUTCOMES), 'provider_receipt_limit': _PROVIDER_RECEIPT_LIMIT,
        'source_binding': original, 'selected_requirement_ids': list(plan.requirement_ids),
        'adapter_family': 'OwnedLocalCaseAdapter', 'legacy_inventory': legacy,
        'runtime_dir': str(state_dir.resolve()),
    }
    runtime = identities = sandbox = None
    case = {}
    error = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        try:
            with OwnedLocalJudgeSandbox(run_id, judge_outcomes=_JUDGE_OUTCOMES,
                                        provider_receipt_limit=_PROVIDER_RECEIPT_LIMIT) as sandbox_value:
                sandbox = sandbox_value
                with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                     root=state_dir, run_id=run_id, store_messages=True,
                                     loop_execution_enabled=True,
                                     judge_sandbox=sandbox_value) as runtime_value:
                    runtime = runtime_value
                    write(runtime.root/'fixed-loop-binding.json', binding)
                    fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                    with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities_context:
                        identities = identities_context
                        fixtures.apply(allow_write=True, role_identity=identities)
                        for role in fixtures.roles:
                            identities.verify(role)
                        adapter = OwnedFixedLoopCaseAdapter(runtime, sandbox_value, identities)
                        execution = execute_fixed_loop_case(adapter)
                        case = execution.snapshot()
                        case.update(schema_version=1, run_id=run_id, case_id=CASE_ID,
                                    executor_digest=source,
                                    required_assertion_count=REQUIRED_ASSERTION_COUNT,
                                    assertions=[{'id': name, 'status': 'PASS'}
                                                for name in ASSERTION_IDS]
                                    if case.get('status') == 'PASS' else [])
                        write(runtime.root/'local-fixed-loop.json', case)
        except BaseException as failure:
            error = failure
        if runtime is None:
            if error is not None:
                raise error
            raise RunStateError('Fixed Loop owned runtime was not created')
        if not case and (runtime.root/'local-fixed-loop.json').is_file():
            saved = read(runtime.root/'local-fixed-loop.json')
            if (saved.get('case_id') == CASE_ID and saved.get('run_id') == run_id
                    and saved.get('executor_digest') == source):
                case = saved
        shutdown = runtime._record()
        credentials = identities.summary() if identities is not None else {'credential_memory': 'NOT_CAPTURED'}
        judge_closed = sandbox.snapshot() if sandbox is not None else {'state': 'NOT_CREATED'}
        passed = (error is None and executor_digest() == source
                  and read(runtime.root/'fixed-loop-binding.json') == binding
                  and bool(case) and read(runtime.root/'local-fixed-loop.json') == case
                  and case.get('status') == 'PASS'
                  and case.get('completed_count') == len(plan.requirements)
                  and [step.get('requirement_id') for step in case.get('steps', [])] == list(plan.requirement_ids)
                  and all(step.get('status') == 'PASS' for step in case.get('steps', []))
                  and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
                  and credentials.get('credential_memory') == 'CLEARED'
                  and judge_closed.get('state') == 'CLOSED' and judge_closed.get('failed') is False)
        status = ('CANCELLED' if error is not None and has_cancellation(error)
                  else 'LOCAL_FIXED_LOOP_PASS' if passed else 'BLOCKED')
        result = {
            'schema_version': 1, 'status': status, 'run_id': run_id, 'case_id': CASE_ID,
            'executor_digest': source, 'case': case, 'shutdown': shutdown,
            'credentials': credentials, 'judge_sandbox': judge_closed,
            'remote_validation': 'NOT_RUN', 'full_release_status': 'NOT_QUALIFIED',
            'ready_to_execute': False,
        }
        if error is not None:
            result['error_type'] = type(error).__name__
        write(runtime.root/'fixed-loop-flow-report.json', result)
        from .local_fixed_loop_reporting import publish_fixed_loop
        canonical = publish_fixed_loop(runtime, binding, result)
        result.update(canonical_summary=canonical['summary'], outputs=canonical['outputs'])
        if canonical['status'] not in ('LOCAL_SUITE_PASS', 'CANCELLED'):
            result['status'] = 'BLOCKED'
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)
