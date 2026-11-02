#!/usr/bin/env python3
"""Summarize local fault injection JSON without exposing actor or session IDs."""
import argparse
import json
from pathlib import Path
import statistics
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', type=Path, help='read-only BCS database for delivery counts')
    parser.add_argument('files', type=Path, nargs='+')
    args = parser.parse_args()
    rows = []
    for path in args.files:
        data = json.loads(path.read_text())
        start, end = data.get('T0_kill_ms'), data.get('T4_new_run_running_ms')
        elapsed = end - start if start is not None and end is not None else None
        checks = None
        if args.sqlite:
            with sqlite3.connect(f'file:{args.sqlite}?mode=ro', uri=True) as db:
                msg = 'run-result:' + data['run_id']
                checks = {'messages': db.execute('SELECT COUNT(*) FROM bcs_messages WHERE message_id=?', (msg,)).fetchone()[0],
                          'deliveries': db.execute("SELECT COUNT(*) FROM bcs_message_deliveries WHERE source_message_id=? AND kind='send'", (msg,)).fetchone()[0],
                          'completed_deliveries': db.execute("SELECT COUNT(*) FROM bcs_message_deliveries WHERE source_message_id=? AND kind='send' AND status='completed'", (msg,)).fetchone()[0],
                          'failed_events': db.execute("SELECT COUNT(*) FROM bcs_events WHERE run_id=? AND event_type='state_machine.run.failed'", (data['run_id'],)).fetchone()[0]}
        rows.append({'checks': checks, 'case': path.stem, 'phase': data.get('node_id', '').split('_')[0],
                     'seed': data.get('selection_seed'), 'eligible_count': data.get('eligible_count'),
                     'queue_policy': data.get('queue_policy'), 'kill_to_successor_ms': elapsed,
                     'passed_60s': data.get('passed_60s', False),
                     'delivery_status': data.get('delivery_status'),
                     'restart_online': data.get('worker_restart_online_ms') is not None})
    times = [r['kill_to_successor_ms'] for r in rows if r['kill_to_successor_ms'] is not None]
    result = {'cases': rows, 'total': len(rows), 'passed': sum(r['passed_60s'] for r in rows),
              'missing_successor': sum(r['kill_to_successor_ms'] is None for r in rows),
              'min_ms': min(times) if times else None, 'max_ms': max(times) if times else None,
              'median_ms': statistics.median(times) if times else None}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
