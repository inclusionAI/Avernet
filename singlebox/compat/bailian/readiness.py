"""Combine scoped evidence without treating missing/skipped checks as ready."""
import argparse
import json
import os
from pathlib import Path

from configure import ENGINES


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def summarize(directory):
    rows = []
    for engine in ENGINES:
        smoke_dir = directory / ('bailian-smoke-' + engine)
        e2e_dir = directory / ('bailian-e2e-' + engine)
        smoke, e2e = read(smoke_dir / 'result.json'), read(e2e_dir / 'e2e.json')
        install = read(e2e_dir / 'install.json')
        versions = read(e2e_dir / 'versions.json')
        ready = smoke.get('status') == 'PASS' and e2e.get('status') == 'PASS'
        failed = any(item.get('status') == 'FAIL' for item in (smoke, e2e, install))
        reason = (install.get('reason') if install.get('status') == 'FAIL' else
                  e2e.get('stage') if e2e.get('status') == 'FAIL' else
                  smoke.get('reason') if smoke.get('status') != 'PASS' else None)
        rows.append({'engine': engine, 'selection': versions.get('engine', {}).get('selection', 'unknown'), 'engine_version': versions.get('engine', {}).get('version', 'unknown'),
                     'plugin_version': versions.get('plugin', {}).get('version', 'bcs-bridge'),
                     'commit': versions.get('commit', ''),
                     'smoke': smoke.get('status', 'UNKNOWN'), 'e2e': e2e.get('status', 'UNKNOWN'),
                     'readiness': 'READY' if ready else 'NOT_READY' if failed else 'UNKNOWN',
                     'reason': 'all_scoped_cases_passed' if ready else reason or 'missing_evidence'})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = summarize(args.artifacts)
    for gate in ('HARNESS_RESULT', 'BUILD_RESULT'):
        if gate in os.environ and os.environ[gate] != 'success':
            for row in rows:
                if row['readiness'] == 'READY':
                    row.update(readiness='UNKNOWN', reason=gate.lower() + '_not_successful')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'readiness.json').write_text(json.dumps(rows, indent=2) + '\n')
    lines = ['| Engine | Version | Selection | Plugin / bridge | Model smoke | BCS E2E | Ready | Reason |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in rows:
        lines.append('| ' + ' | '.join(str(row[key]) for key in (
            'engine', 'engine_version', 'selection', 'plugin_version', 'smoke', 'e2e', 'readiness', 'reason')) + ' |')
    lines += ['', 'READY covers connection, file tools, session recall/isolation, restart recovery, tool-event parsing and cancellation.',
              'Group routing and manager/worker tasks are outside this case set. Fixed selections do not certify latest engines.']
    markdown = '\n'.join(lines) + '\n'
    (args.output / 'readiness.md').write_text(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(markdown)
    return 0 if all(row['readiness'] == 'READY' for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
