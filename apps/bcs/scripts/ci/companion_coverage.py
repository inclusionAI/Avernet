#!/usr/bin/env python3
"""Build once, then supervise isolated Caller/OAuth producers before aggregation.

Every subprocess has an owned process group. Cancellation terminates those
children (including test servers), never the canonical Singlebox stack. Logs
and timings are separate from the parent's TSV and shared HTTP/CLI evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class Cancelled(Exception):
    def __init__(self, signum: int):
        self.signum = signum


def cancel(signum, _frame):
    raise Cancelled(signum)


def signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def supervise(bcs_dir: Path, report_dir: Path) -> int:
    report_dir.mkdir(parents=True, exist_ok=True)
    oauth_report = bcs_dir / 'target/cov-e2e/oauth_e2e.json'
    oauth_report.parent.mkdir(parents=True, exist_ok=True)
    oauth_report.write_text('{"status":"failed","scenarios":[],"checks":[]}\n')
    result = {'status': 'failed', 'phases': {}}
    processes = []
    logs = []
    starts = {}

    def start(name: str, command: list[str]) -> subprocess.Popen:
        log = (report_dir / f'{name}.log').open('w')
        logs.append(log)
        starts[name] = time.monotonic()
        print(f'[companion] {name}: start', flush=True)
        process = subprocess.Popen(command, cwd=bcs_dir, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        processes.append(process)
        return process

    def finish(name: str, code: int) -> None:
        elapsed = round(time.monotonic() - starts[name], 3)
        result['phases'][name] = {'exit_code': code, 'duration_seconds': elapsed}
        print(f'[companion] {name}: exit={code}, elapsed={elapsed}s', flush=True)

    old_handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        build = start('companion-build', ['bash', str(bcs_dir / 'scripts/build_e2e_companions.sh'), 'all'])
        code = build.wait()
        finish('companion-build', code)
        if code:
            return code
        pending = {
            name: start(name, ['bash', str(bcs_dir / f'scripts/{name}_e2e_coverage.sh'), '--run-only'])
            for name in ('caller', 'oauth')
        }
        # Poll both so timing reflects actual completion, not sequential wait
        # order. A failure never cancels the other producer's diagnostic run.
        while pending:
            for name, process in list(pending.items()):
                code = process.poll()
                if code is not None:
                    finish(name, code)
                    del pending[name]
            if pending:
                time.sleep(0.1)
        code = result['phases']['caller']['exit_code'] or result['phases']['oauth']['exit_code']
        if code == 0:
            result['status'] = 'passed'
        return code
    except Cancelled as exc:
        result['cancelled_by_signal'] = exc.signum
        return 128 + exc.signum
    except OSError as exc:
        result['error'] = str(exc)
        print(f'companion launch failed: {exc}', file=sys.stderr)
        return 1
    finally:
        # Ignore repeated interrupts while reaping. Signal groups even if the
        # wrapper exited already: descendants must not outlive this supervisor.
        for sig in old_handlers:
            signal.signal(sig, signal.SIG_IGN)
        for process in processes:
            signal_group(process, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal_group(process, signal.SIGKILL)
                process.wait()
            signal_group(process, signal.SIGKILL)
        for log in logs:
            log.close()
        (report_dir / 'companion-timings.json').write_text(json.dumps(result, indent=2) + '\n')
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bcs-dir', required=True, type=Path)
    parser.add_argument('--report-dir', required=True, type=Path)
    args = parser.parse_args()
    return supervise(args.bcs_dir.resolve(), args.report_dir.resolve())


if __name__ == '__main__':
    raise SystemExit(main())
