"""Require a complete Gateway–Worker process report before passing BCS E2E."""
import json
from pathlib import Path
import sys


REQUIRED_CHECKS = {
    'direct_admission_stream_and_reply',
    'direct_confirmed_cancel_preserves_context',
    'direct_mixed_parts_survive_blob_and_final_status',
    'gateway_http_retry_conflict_once',
    'gateway_http_concurrent_first_request',
    'gateway_http_sse_get_task_consistency',
    'document_projection_atomic_patch_and_unknown',
    'finish_archive_restore_and_late_cancel',
    'append_archive_restore_and_late_cancel',
    'cancel_independent_of_runner',
    'quarantine_and_control_authorization',
    'blocked_and_control_authorization',
    'memory_store_worker_takeover',
    'graceful_handoff_preserves_pending_task',
    'human_reply_dispatch_input_once',
    'holder_input_acceptance_and_cancel_unknown',
    'live_follow_snapshot_trim_gap_restore',
    'sending_unknown_late_evidence_without_rerun',
}


def main(path: Path) -> int:
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        print(f'BCS Worker E2E report is unreadable ({path}): {error}', file=sys.stderr)
        return 1
    if not isinstance(report, dict) or report.get('status') != 'passed':
        print('BCS Worker E2E did not pass', file=sys.stderr)
        return 1
    if report.get('store') != 'memory_mock' or report.get('durable_store') is not False:
        print('BCS Worker E2E must identify its memory-only Store boundary', file=sys.stderr)
        return 1
    if report.get('archive') != 'sqlite':
        print('BCS Worker E2E did not use the existing SQLite archive', file=sys.stderr)
        return 1
    checks = report.get('checks')
    observed = {item for item in checks if isinstance(item, str)} if isinstance(checks, list) else set()
    if missing := REQUIRED_CHECKS - observed:
        print(f'BCS Worker E2E missing scenarios: {", ".join(sorted(missing))}', file=sys.stderr)
        return 1
    # Singlebox already preserves the BCS story output in bcs/e2e.log.
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1])))
