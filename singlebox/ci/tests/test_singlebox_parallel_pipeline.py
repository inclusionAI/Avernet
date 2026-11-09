"""Canonical shell pipeline with executable, isolated process-boundary fixtures."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def pipeline(tmp_path):
    # Reuse the existing full stack/coverage fixtures, without executing its
    # legacy test entrypoints or needing a real Singlebox service/venv.
    source = (ROOT / 'singlebox/verity/test_singlebox_coverage_gate.sh').read_text()
    source = source.split('\ntest_default_mode_runs_real_singlebox\n', 1)[0]
    source = source.replace('ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
                            f'ROOT={shlex.quote(str(ROOT))}')
    subprocess.run(['bash', '-c', source + f'\nmake_fake_repo {shlex.quote(str(tmp_path))}'], check=True)
    scripts = tmp_path / 'apps/bcs/scripts'
    (scripts / 'ci').mkdir(exist_ok=True)
    (scripts / 'ci/companion_coverage.py').write_text('''import json, os, signal, time
from pathlib import Path
root = Path(os.environ['CASE_ROOT'])
(root / 'companion-started').touch()
for _ in range(500):
    if (root / 'shared-finished').exists(): break
    time.sleep(.02)
else: raise SystemExit(50)
(root / 'companion-finished').touch()
report = root / 'singlebox/.dependencies/coverage/singlebox/reports/bcs'
(report / 'companion-timings.json').write_text('{"status":"passed"}')
raise SystemExit(int(os.environ.get('COMPANION_RC', '0')))
''')
    uv = tmp_path / 'fake-bin/uv'
    s = uv.read_text().replace('  junit=""', '''  for attempt in {1..100}; do
    [[ -f "$CASE_ROOT/companion-started" ]] && break
    sleep 0.02
  done
  test -f "$CASE_ROOT/companion-started"
  touch "$CASE_ROOT/acceptance-started"
  junit=""''')
    uv.write_text(s)
    e2e = scripts / 'e2e_coverage.sh'
    e2e_source = e2e.read_text()
    start = e2e_source.index('if [[ "$*" == *--shared-tests-only* ]]')
    end = e2e_source.index('cov_dir=', start)
    e2e_source = e2e_source[:start] + e2e_source[end:]
    s = e2e_source.replace('cov_dir="${PWD}/apps/bcs/target/cov-e2e"', '''if [[ "$*" == *--shared-tests-only* ]]; then
  test -f "$CASE_ROOT/acceptance-started"
  touch "$CASE_ROOT/shared-finished"
  exit "${SHARED_RC:-0}"
fi
test -f "$CASE_ROOT/companion-finished"
echo aggregate >> "$CASE_ROOT/order"
cov_dir="${PWD}/apps/bcs/target/cov-e2e"''')
    e2e.write_text(s)
    return tmp_path


@pytest.mark.parametrize('companion_rc,shared_rc', [('0', '0'), ('17', '0'), ('0', '19')])
def test_overlap_join_before_aggregation_and_failure_status(pipeline, companion_rc, shared_rc):
    root = pipeline
    env = {**os.environ, 'PATH': str(root / 'fake-bin') + ':' + os.environ['PATH'],
           'PYTHON': sys.executable, 'CASE_ROOT': str(root),
           'SINGLEBOX_STUB_LOG': str(root / 'singlebox.log'), 'UV_STUB_LOG': str(root / 'uv.log'),
           'COMPANION_RC': companion_rc, 'SHARED_RC': shared_rc}
    result = subprocess.run(['bash', 'singlebox/ci/singlebox_coverage.sh'], cwd=root,
                            env=env, capture_output=True, text=True, timeout=30)
    expected = int(shared_rc) or int(companion_rc)
    assert result.returncode == expected, result.stdout + result.stderr
    assert (root / 'order').read_text().splitlines() == ['aggregate']
    reports = root / 'singlebox/.dependencies/coverage/singlebox/reports'
    assert (reports / 'bcs/companion-timings.json').exists()
    assert (reports / 'bcs/cobertura.xml').exists()
    summary = json.loads((reports / 'summary.json').read_text())
    assert summary['systems']['bcs']['e2e_status'] == ('passed' if expected == 0 else 'failed')


def test_parent_cancellation_joins_supervisor_before_stack_cleanup(pipeline):
    import signal
    import time

    root = pipeline
    supervisor = root / 'apps/bcs/scripts/ci/companion_coverage.py'
    supervisor.write_text('''import os, signal, time
from pathlib import Path
root=Path(os.environ['CASE_ROOT'])
def stop(_sig,_frame):
    (root / 'companion-cancelled').touch()
    raise SystemExit(143)
signal.signal(signal.SIGTERM, stop)
(root / 'companion-started').touch()
while True: time.sleep(.02)
''')
    launcher = root / 'singlebox/singlebox.sh'
    launcher.write_text(launcher.read_text().replace('printf \'%s\\n\' "$*"', '''if [[ "$*" == "--standalone stop all" ]]; then
  test -f "$CASE_ROOT/companion-cancelled" || exit 88
  touch "$CASE_ROOT/cleanup-after-cancel"
fi
printf '%s\\n' "$*"'''))
    env = {**os.environ, 'PATH': str(root / 'fake-bin') + ':' + os.environ['PATH'],
           'PYTHON': sys.executable, 'CASE_ROOT': str(root),
           'SINGLEBOX_STUB_LOG': str(root / 'singlebox.log'), 'UV_STUB_LOG': str(root / 'uv.log')}
    with (root / 'run.log').open('w') as log:
        process = subprocess.Popen(['bash', 'singlebox/ci/singlebox_coverage.sh'], cwd=root,
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while not (root / 'shared-finished').exists():
                assert process.poll() is None, (root / 'run.log').read_text()
                assert time.monotonic() < deadline
                time.sleep(.02)
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=15) == 143
            assert (root / 'companion-cancelled').exists()
            assert (root / 'cleanup-after-cancel').exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
