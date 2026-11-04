"""No-delete control-plane subset using the legacy pilot's business steps.

Local/mock and separately reviewed gateway attachment share these steps and
durable retained evidence. This executor never infers remote terminal state,
deletes data or releases a reservation. Terminal observation/reuse is pending.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import signal
import time

from .adapters import adapter_for
from .case_scope import CaseScopedAdapter
from .control_steps import ControlProgress, PilotError, group_control_steps, session_control_steps
from .correlation import CorrelatedEvidence
from .errors import has_cancellation, primary_error
from .execution_ledger import ExecutionLedger
from .file_steps import FileProgress, file_control_steps
from .inventory import current_manifest, executable_case
from .ledger import Ledger
from .observation_gate import AdmittedObserver, observer_admission_diagnostic
from .operation_authority import AuthorizedAdapter
from .pool import PoolLease, bound_pool_path
from .prepare import prepare_run
from .profile import ConfigError, identifier
from .retained_contract import suite_for_case, validate_resource_snapshot
from .remote_effects import require_group_effect_admission
from .retained_remote import (RetainedReview, adapter_factory as remote_adapter_factory,
                              preflight as remote_preflight)
from .retained_file_remote import (CONTRACT as FILE_REVIEW_CONTRACT, FileRetainedReview,
    adapter_factory as remote_file_adapter_factory, preflight as remote_file_preflight)
from .reporting import redact
from .retention_quota import reserve as reserve_retention_quota
from .run_state import read, require_profile_binding, write

def _file_control_steps(*args, **kwargs):
    return file_control_steps(*args, **kwargs, expected_backend='local')


def _remote_file_control_steps(*args, **kwargs):
    return file_control_steps(*args, **kwargs, expected_backend='baas')


CASES = {
    'RETAIN-GROUP-01': (ControlProgress, group_control_steps),
    'RETAIN-SESSION-01': (ControlProgress, session_control_steps),
    'RETAIN-FILE-01': (FileProgress, _file_control_steps),
}
SUITE = 'preprod-retained-v1'


@dataclass
class _Cancellation:
    requested: bool = False
    finalizing: bool = False
    sealed: bool = False
    execution_entered: bool = False


def _validate_entry(profile: dict, run_id: str, case_id: str,
                    review: RetainedReview | FileRetainedReview | None = None) -> None:
    identifier(run_id, 'retained Run ID')
    if case_id not in CASES:
        raise ConfigError('unknown retained Case')
    # A separate scoped review admits only this supervised retained path; the
    # original PRE bootstrap gate and native identity boundary are unchanged.
    if review is not None:
        if (review.run_id != run_id or review.case_id != case_id or review.profile != profile):
            raise ConfigError('retained admission object differs from the selected Run/Profile/Case')
        review.validate()
        return
    try:
        require_group_effect_admission(profile)
    except RuntimeError:
        raise ConfigError('retained remote execution requires a current scoped review and observer binding') from None
    if profile['entrypoint']['kind'] != 'mock' or profile['environment']['tier'] != 'local':
        raise ConfigError('retained command currently requires the local/mock contract adapter')


def run_retained_case(profile: dict, state_dir: Path, run_id: str, case_id: str, *,
                      adapter_factory=None, _cancellation: _Cancellation | None = None,
                      _remote_review: RetainedReview | FileRetainedReview | None = None,
                      _executable: Path | None = None,
                      _held_lease: PoolLease | None = None) -> dict:
    """Execute once in a prepared retained Run; never release its shared pool."""
    cancellation = _cancellation if _cancellation is not None else _Cancellation()
    _validate_entry(profile, run_id, case_id, _remote_review)
    if isinstance(_remote_review, FileRetainedReview) and _held_lease is None:
        raise ConfigError('remote retained File requires the continuously held serial trigger lease')
    if _remote_review is not None:
        if _executable is None or adapter_factory is not None:
            raise ConfigError('remote retained execution requires the reviewed MCP composition, not an injected adapter')
        selected_factory = (remote_file_adapter_factory if case_id == 'RETAIN-FILE-01'
                            else remote_adapter_factory)
        def factory(profile, *, state_dir, case_id):
            return selected_factory(profile, state_dir, run_id, case_id, executable=_executable)
    else:
        factory = adapter_factory or adapter_for
    state = read(state_dir / 'run.json')
    digest = require_profile_binding(state, profile)
    definition = executable_case(state_dir, state, case_id)
    if definition.get('finalization') != 'retain' or state.get('run_id') != run_id:
        raise ConfigError('retained Run or Case binding differs')
    if state.get('status') != 'PREPARED' or state.get('attempted_cases'):
        raise ConfigError('retained Run has unresolved or attempted work; do not retry or reuse the pool')
    if _held_lease is not None:
        _held_lease.require_held()
        if (_held_lease.path != bound_pool_path(state) or _held_lease.run_id != run_id
                or _held_lease.profile_digest != digest):
            raise ConfigError('held serial lease does not match retained execution')
    lease = _held_lease or PoolLease.from_file(bound_pool_path(state), run_id, digest)
    try:
        # Revalidate while holding the command guard, not from an earlier read.
        state = read(state_dir / 'run.json')
        require_profile_binding(state, profile)
        definition = executable_case(state_dir, state, case_id)
        if (state.get('status') != 'PREPARED' or state.get('attempted_cases')
                or state.get('run_id') != run_id or definition.get('finalization') != 'retain'):
            raise ConfigError('retained Run changed before execution')
        if isinstance(_remote_review, FileRetainedReview):
            from .retained_serial import policy_view
            policy_view(profile, state, state_dir)
        with Ledger(Path(state['ledger_path']), run_id, digest) as ledger:
            # Mark before effects so legacy cleanup/release cannot downgrade a
            # retained Run to a resource-only Run on failure or restart.
            execution = ExecutionLedger(ledger)
            return _execute(profile, state_dir, run_id, definition, state, ledger, execution,
                            lease, factory, cancellation,
                            remote_review=_remote_review)
    except BaseException:
        if state.get('status') == 'RUNNING':
            state.update(status='QUARANTINED', quiescence='UNKNOWN')
            write(state_dir / 'run.json', state)
        raise
    finally:
        if _held_lease is None:
            lease.close()  # Only the process guard; durable reservation stays held.


def _execute(profile, state_dir, run_id, definition, state, ledger, execution, lease, factory, cancellation,
             *, remote_review: RetainedReview | FileRetainedReview | None = None):
    case_id = definition['id']
    progress_type, steps = CASES[case_id]
    summary = state.get('retained_review')
    if (isinstance(remote_review, FileRetainedReview) and case_id == 'RETAIN-FILE-01'
            and profile['entrypoint']['kind'] in {'gateway', 'bcs-direct'}
            and isinstance(summary, dict) and summary.get('contract') == FILE_REVIEW_CONTRACT):
        steps = _remote_file_control_steps
    progress = progress_type()
    evidence = CorrelatedEvidence(ledger, case_id, 1)
    assertions, semantic = [], []
    functional = 'NOT_RUN'
    business_completed = False
    failure = None
    authority = scoped = None
    execution_observer = None
    execution_observation = {'status': 'NOT_CONFIGURED', 'scope_status': 'UNCONFIRMED'}
    socket_status = 'NOT_OPENED'
    retention = {'schema_version': 1, 'run_id': run_id, 'profile_digest': state['profile_digest'],
                 'case_id': case_id, 'policy': 'retain', 'owner': profile['human']['user_id'],
                 'review_after_epoch': int(time.time()) + profile['policy']['failure_retention_hours'] * 3600,
                 'automatic_expiry': False, 'resources': [], 'inventory_status': 'PENDING',
                 'manual_action': 'Review exact owned resources and execution evidence; no automatic deletion or pool reuse.'}
    if 'retention_quota' in state:
        retention['retention_quota'] = state['retention_quota']
    write(state_dir / 'retention.json', retention)

    def assertion(name, passed, detail=''):
        assertions.append({'id': f'{case_id}.{name}', 'status': 'PASS' if passed else 'FAIL', 'detail': detail})
        if not passed:
            raise PilotError('retained business assertion failed')

    try:
        raw = factory(profile, state_dir=state_dir, case_id=case_id)
        authority = AuthorizedAdapter(raw, lease)
        authority.require_active()
        if callable(getattr(raw, 'bind_execution_ledger', None)):
            raw.bind_execution_ledger(execution, case_id, 1)
        scoped = CaseScopedAdapter(authority, ledger, state['profile_digest'], definition, attempt=1)
        observer = AdmittedObserver(scoped, profile, case_id, resource_scope=scoped.observation_scope)
        observer_factory = getattr(authority, 'execution_observer', None)
        if callable(observer_factory):
            execution_observer = observer_factory()
            if execution_observer is not None:
                execution_observation = {'status': 'NOT_RUN', 'scope_status': 'UNCONFIRMED'}
                execution_observer.admit()
        if remote_review is not None:
            # Admission is read-only. Charge only after both observers are ready,
            # but before Story steps can create resource intents or issue writes.
            # The journal and Run checkpoint are not a cross-file transaction:
            # any attempted reservation stays uncertain until its receipt is saved.
            authority.require_active()
            remote_review.validate()
            state['retention_quota'] = {'status': 'UNCONFIRMED', 'automatic_refund': False}
            retention['retention_quota'] = state['retention_quota']
            write(state_dir / 'run.json', state)
            receipt = reserve_retention_quota(profile, remote_review, lease, case_id)
            confirmed = dict(state, retention_quota=receipt)
            write(state_dir / 'run.json', confirmed)
            state.update(confirmed)
            retention['retention_quota'] = receipt
            write(state_dir / 'retention.json', retention)
            authority.require_active()
        cancellation.execution_entered = True
        state.update(status='RUNNING', attempted_cases=[case_id], active_case=case_id,
                     finalization_policy='retain', quiescence='UNKNOWN', execution_contract_required=True)
        write(state_dir / 'run.json', state)
        functional = 'FAIL'
        steps(profile, run_id, case_id, ledger=ledger, adapter=scoped, observer=observer,
              control=progress, evidence=evidence, assertions=assertions,
              semantic_evidence=semantic, assertion=assertion)
        functional = 'PASS'
        business_completed = True
        if execution_observer is not None:
            execution_observation = execution_observer.observe(
                ledger, scoped.observation_scope, authority.require_active, lease=lease, case_id=case_id,
                timeout_seconds=profile['policy']['timeout_seconds'])
    except (Exception, KeyboardInterrupt) as error:
        cancellation.finalizing = True
        cancellation.requested = cancellation.requested or has_cancellation(error)
        if business_completed:
            partial = getattr(primary_error(error), 'execution_observation', None)
            execution_observation = partial if isinstance(partial, dict) else {
                'status': 'CANCELLED' if has_cancellation(error) else 'BLOCKED',
                'scope_status': 'UNCONFIRMED', 'error_type': type(error).__name__}
        else:
            functional = 'CANCELLED' if has_cancellation(error) else 'NOT_RUN' if functional == 'NOT_RUN' else 'FAIL'
        if not cancellation.execution_entered:
            cancellation.finalizing = True
            return _preexecution_failure(
                state_dir, run_id, case_id, 'pre-effect-admission', error,
                cancellation, held_lease=lease)
        failure = {'error_type': type(error).__name__}
        diagnostic = observer_admission_diagnostic(error)
        if diagnostic is not None:
            failure['diagnostic'] = diagnostic
        evidence.record_failure(error)
    finally:
        cancellation.finalizing = True
        if progress.ws_attempted:
            socket_status = 'UNCONFIRMED'
            if not progress.ws_open:
                attempts = [r for r in evidence if r.get('operation') == 'session.ws.connect']
                if (len(attempts) == 1 and attempts[0].get('outcome') == 'ERROR'
                        and attempts[0].get('socket_close_status') in ('CLOSED', 'NOT_OPENED')):
                    socket_status = attempts[0]['socket_close_status']
        # No quiesce/cancel call and no DELETE, including assertion failure and
        # cancellation. Only our already-held socket is eligible for closing.
        if progress.ws_open:
            socket_status = 'UNCONFIRMED'
            try:
                data, record = scoped.session_ws_close(progress.session_id)
                if not isinstance(data, dict) or data.get('closed') is not True:
                    raise PilotError('owned socket closure was not confirmed')
                progress.ws_open = False
                socket_status = 'CLOSED'
                evidence.append(record)
                assertion('websocket_closed', True)
            except (Exception, KeyboardInterrupt) as error:
                if has_cancellation(error):
                    cancellation.requested = True
                    functional = 'CANCELLED'
                elif functional == 'PASS':
                    functional = 'FAIL'
                failure = failure or {'error_type': type(error).__name__}
                assertions.append({'id': f'{case_id}.socket_finalization', 'status': 'ERROR'})

    if authority is not None:
        try:
            authority.require_active()
        except RuntimeError:
            failure = failure or {'error_type': 'OperationAuthorityLost'}
    if authority is not None and authority.socket_close_status != 'NOT_REQUIRED':
        socket_status = authority.socket_close_status
    # Fail closed against the selected retained Case's exact local inventory;
    # this executor contract is independent of remote retention quota policy.
    snapshot = ledger.resource_snapshot()
    validate_resource_snapshot(case_id, run_id, snapshot, require_complete=functional == 'PASS')
    projection = ('handle', 'case_id', 'attempt', 'kind', 'parent', 'resource_id', 'origin', 'state', 'request_id')
    retention.update(resources=[{key: row[key] for key in projection} for row in snapshot],
                     inventory_status=('UNKNOWN_RESOURCES' if any(r['state'] != 'live'
                         or r['origin'] != 'created' or not r['resource_id'] for r in snapshot)
                         else 'COMPLETE'), disposition='RETAINED_UNRESOLVED')
    write(state_dir / 'retention.json', retention)
    report = {
        'schema_version': 1, 'run_id': run_id, 'case_id': case_id,
        'profile_digest': state['profile_digest'], 'manifest_digest': state['manifest_digest'],
        'executor_digest': state['executor_digest'], 'case_version': definition['version'],
        'status': 'CANCELLED' if cancellation.requested else 'FAIL' if functional == 'FAIL' else 'BLOCKED',
        'ready_to_execute': False, 'functional': functional, 'cleanup': 'RETAINED',
        'execution_status': 'UNCONFIRMED', 'quiescence': 'UNKNOWN',
        'execution_observation': execution_observation,
        'resource_disposition': 'RETAINED_UNRESOLVED', 'socket_status': socket_status,
        'evidence_status': progress.evidence_status, 'evidence_required': definition['required_evidence'],
        'assertions': assertions, 'semantic_evidence': semantic, 'evidence': list(evidence),
        'excluded_assertions': definition['excluded_assertions'], 'failure': failure,
        'pool_disposition': 'QUARANTINED', 'full_release_status': 'NOT_QUALIFIED',
        'operation_authority': 'INVALIDATED' if authority is not None and authority.invalidated else 'NOT_INVALIDATED',
        'note': 'Functional results only; scoped terminal observation and safe pool reuse remain unimplemented.',
    }
    if 'admission_policy' in state:
        from .retained_serial import policy_view
        policy_view(profile, state, state_dir)
        expected = set(definition['assertions'])
        actual_ids = [a['id'] for a in assertions]
        eligible_evidence = {'LOCAL_SURROGATE'} if profile['entrypoint']['kind'] == 'mock' else {'PASS'}
        passes = (functional == 'PASS' and expected <= set(actual_ids)
                  and len(set(actual_ids)) == len(actual_ids)
                  and all(a['status'] == 'PASS' for a in assertions)
                  and progress.evidence_status in eligible_evidence
                  and socket_status in {'NOT_OPENED', 'CLOSED'}
                  and report['operation_authority'] == 'NOT_INVALIDATED')
        report.update(admission_policy=state['admission_policy'],
                      serial_admission_digest=state['serial_admission_digest'],
                      status='CANCELLED' if cancellation.requested else 'PASS' if passes else report['status'],
                      pool_disposition='RETAINED_SERIAL', next_run_admission='FRESH_CHECK_REQUIRED',
                      note='Selected retained Case result; remote closure is diagnostic, not cleanup or full release qualification.')
    for key in ('observer_binding', 'retained_review', 'retention_quota'):
        if key in state:
            report[key] = state[key]
    write(state_dir / f'report-{case_id}.json', redact(report))
    state.update(status='RETAINED' if 'admission_policy' in state else 'QUARANTINED',
                 quiescence='UNKNOWN', cleanup_status='RETAINED',
                 last_report=f'report-{case_id}.json')
    write(state_dir / 'run.json', state)
    # A first signal during evidence publication is deferred, then reflected in
    # the sealed result. No product operation is replayed to update the report.
    cancellation.sealed = True
    if cancellation.requested and report['status'] != 'CANCELLED':
        report['status'] = 'CANCELLED'
        write(state_dir / f'report-{case_id}.json', redact(report))
    if 'admission_policy' in state:
        from .profile import fingerprint
        pending = state_dir / 'serial-finalization-pending.json'
        if read(pending) != {'schema_version': 1, 'contract': state['admission_policy'], 'run_id': run_id}:
            raise ConfigError('serial finalization intent changed')
        if read(state_dir / 'run.json') != state or read(state_dir / f'report-{case_id}.json') != redact(report):
            raise ConfigError('serial final Run/Case publication differs')
        write(state_dir / 'serial-completion.json', {
            'schema_version': 1, 'contract': state['admission_policy'], 'run_id': run_id,
            'state_digest': fingerprint(state), 'report_digest': fingerprint(redact(report))})
        pending.unlink()  # Commit point: no fallible lifecycle publication follows.
    return redact(report)


def _preexecution_failure(state_dir, run_id, case_id, phase, error, cancellation, held_lease=None):
    """Reservation already exists; preserve failure without claiming execution."""
    cancellation.finalizing = True
    cancellation.requested = cancellation.requested or has_cancellation(error)
    errors = []
    failure = {'phase': phase, 'error_type': type(error).__name__}
    diagnostic = observer_admission_diagnostic(error)
    if diagnostic is not None:
        failure['diagnostic'] = diagnostic
    result = {'schema_version': 1, 'run_id': run_id, 'case_id': case_id,
              'status': 'CANCELLED' if cancellation.requested else 'BLOCKED',
              'ready_to_execute': False, 'prepared': True, 'functional': 'NOT_RUN',
              'execution_status': 'UNCONFIRMED', 'quiescence': 'UNKNOWN',
              'pool_disposition': 'RETAINED', 'failure': failure,
              'persistence': 'UNCONFIRMED', 'persistence_errors': errors}
    guard = None
    try:
        state = read(state_dir / 'run.json')
        if state.get('run_id') != run_id:
            raise ConfigError('prepared Run changed before failure checkpoint')
        guard = held_lease or PoolLease.from_file(bound_pool_path(state), run_id, state['profile_digest'])
        guard.require_held()
        state = read(state_dir / 'run.json')
        if state.get('run_id') != run_id or state.get('status') != 'PREPARED':
            raise ConfigError('prepared Run is no longer exclusively awaiting execution')
        if 'retention_quota' in state:
            result['retention_quota'] = state['retention_quota']
        if state.get('attempted_cases'):
            raise ConfigError('prepared Run already attempted business work')
        state.update(status='QUARANTINED', quiescence='UNKNOWN', preparation_failure=result['failure'])
        write(state_dir / 'run.json', state)
        result.update(local_trigger_status='NOT_STARTED', profile_digest=state['profile_digest'],
                      manifest_digest=state['manifest_digest'], executor_digest=state['executor_digest'])
    except (Exception, KeyboardInterrupt) as secondary:
        # Missing/busy ownership or a changed Run cannot establish that another
        # executor did not start. Do not overwrite its state as NOT_RUN.
        result['functional'] = 'UNKNOWN'
        cancellation.requested = cancellation.requested or has_cancellation(secondary)
        errors.append({'phase': 'run-state', 'error_type': type(secondary).__name__})
    finally:
        if guard is not None and held_lease is None:
            guard.close()
    result.update(status='CANCELLED' if cancellation.requested else 'BLOCKED',
                  persistence='PARTIAL' if errors else 'PERSISTED')
    try:
        write(state_dir / 'retained-run.json', result)
    except (Exception, KeyboardInterrupt) as secondary:
        cancellation.requested = cancellation.requested or has_cancellation(secondary)
        errors.append({'phase': 'lifecycle-receipt', 'error_type': type(secondary).__name__})
        result['persistence'] = 'FAILED'
    cancellation.sealed = True
    if cancellation.requested and result['status'] != 'CANCELLED':
        result['status'] = 'CANCELLED'
        try:
            write(state_dir / 'retained-run.json', result)
        except (Exception, KeyboardInterrupt) as secondary:
            errors.append({'phase': 'cancellation-checkpoint', 'error_type': type(secondary).__name__})
            result['persistence'] = 'FAILED'
    return result


def retained_run(profile: dict, *, state_dir: Path, run_id: str, case_id: str,
                 allow_write: bool, confirm_run_id: str, retained_review: Path | None = None,
                 observer_binding: Path | None = None, executable: Path | None = None,
                 allow_http_read: bool = False, allow_observer_read: bool = False,
                 serial_preflight: bool = False, previous_state_dir: Path | None = None,
                 previous_profile: Path | None = None, profile_transition_review: Path | None = None) -> dict:
    remote = profile['entrypoint']['kind'] in {'gateway', 'bcs-direct'}
    review = None
    if remote:
        if (retained_review is None or observer_binding is None or executable is None
                or allow_http_read is not True or allow_observer_read is not True):
            raise ConfigError('remote retained-run requires retained/observer reviews, explicit MCP executable and HTTP/MCP read permission')
        if case_id == 'RETAIN-FILE-01':
            if serial_preflight is not True:
                raise ConfigError('remote retained File execution requires serial-preflight and quota v2')
            review = FileRetainedReview(profile, run_id, case_id, retained_review)
        else:
            review = RetainedReview(profile, run_id, case_id, retained_review)
    elif any(value is not None for value in (retained_review, observer_binding, executable)):
        raise ConfigError('local retained execution must not accept remote review inputs')
    _validate_entry(profile, run_id, case_id, review)
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('retained-run requires explicit writes and exact Run confirmation')
    if state_dir.exists() or state_dir.is_symlink():
        raise ConfigError('retained-run requires a fresh state directory')
    if previous_state_dir is not None and serial_preflight is not True:
        raise ConfigError('previous retained state requires explicit serial-preflight policy')
    transition = None
    if previous_profile is not None or profile_transition_review is not None:
        if (previous_profile is None or profile_transition_review is None
                or previous_state_dir is None or serial_preflight is not True):
            raise ConfigError('profile transition requires paired previous-profile/review and serial previous-state-dir')
        from .profile_transition import ProfileTransition
        transition = ProfileTransition(profile, run_id, previous_profile, profile_transition_review)
    serial = None
    held_lease = None
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    cancellation = _Cancellation()
    prepared = False
    phase = 'preflight'

    def cancel(_signum, _frame):
        if cancellation.sealed:
            return
        first = not cancellation.requested
        cancellation.requested = True
        if first and not cancellation.finalizing:
            raise KeyboardInterrupt()

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        discovery = None
        if serial_preflight:
            from .retained_serial import SerialAdmission
            serial = SerialAdmission(profile, run_id, state_dir, previous_state_dir, transition=transition)
            serial.__enter__()
        if review is not None:
            preflight = remote_file_preflight if case_id == 'RETAIN-FILE-01' else remote_preflight
            discovery = preflight(profile, review, observer_binding, executable)
        elif serial is not None:
            from .adapters import LocalMockAdapter
            from .profile import fingerprint
            discovery = LocalMockAdapter(profile).preflight()
            discovery['profile_digest'] = fingerprint(profile)
        if discovery is not None and discovery['status'] != 'DISCOVERY_PASSED':
            return {'status': 'BLOCKED', 'ready_to_execute': False, 'functional': 'NOT_RUN',
                    'prepared': False, 'preflight': discovery, 'run_id': run_id, 'case_id': case_id}
        if serial is not None:
            held_lease = serial.reserve(discovery)
        phase = 'prepare'
        prepare_run(profile, state_dir, run_id, manifest=current_manifest(suite_for_case(case_id)),
                    observer_binding=observer_binding, _held_lease=held_lease)
        prepared = True
        if serial is not None:
            from .retained_serial import CONTRACT
            from .profile import fingerprint
            state = read(state_dir / 'run.json')
            state.update(admission_policy=CONTRACT,
                         serial_admission_digest=fingerprint(read(state_dir / 'serial-admission.json')))
            write(state_dir / 'run.json', state)
            write(state_dir / 'serial-finalization-pending.json', {
                'schema_version': 1, 'contract': CONTRACT, 'run_id': run_id})
        phase = 'review-freeze'
        if review is not None:
            # Freeze, then recheck, before constructing any writable adapter.
            # Failure retains the prepared reservation for explicit inspection.
            review.freeze(state_dir)
            state = read(state_dir / 'run.json')
            if state.get('observer_binding') != discovery['observer_binding']:
                raise ConfigError('observer mapping review changed between preflight and preparation')
            phase = 'preflight-checkpoint'
            write(state_dir / 'preflight.json', discovery)
        phase = 'case-execution'
        result = run_retained_case(profile, state_dir, run_id, case_id, _cancellation=cancellation,
                                   _remote_review=review, _executable=executable, _held_lease=held_lease)
        if serial is not None:
            from .reporting import build_report, write_reports
            result['serial_admission'] = read(state_dir / 'serial-admission.json')
            summary = build_report(profile, state_dir)
            result['canonical_summary_status'] = summary['status']
            write_reports(summary, state_dir / 'reports', {'all'})
        return result
    except (Exception, KeyboardInterrupt) as error:
        if prepared and not cancellation.execution_entered:
            return _preexecution_failure(state_dir, run_id, case_id, phase, error, cancellation, held_lease)
        raise
    finally:
        if serial is not None:
            serial.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
