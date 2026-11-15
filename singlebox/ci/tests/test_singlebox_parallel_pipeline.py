"""Canonical shell pipeline with executable, isolated process-boundary fixtures."""
import json
import os
from pathlib import Path
import shlex
import signal
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
    s = s.replace('      [ -n "${COVERAGE_FILE:-}" ] || exit 22', '''      [ -n "${COVERAGE_FILE:-}" ] || exit 22
      # `coverage combine` deletes its raw inputs, so the services must already
      # have exited and saved their final shutdown/lifespan coverage.
      test -f "$CASE_ROOT/python-services-stopped"
      component="$(basename "$COVERAGE_FILE" .coverage)"
      # Mirror real combine input deletion so a stale post-combine flush shows
      # up as recreated raw files instead of passing silently.
      rm -f "$CASE_ROOT/singlebox/.dependencies/coverage/singlebox/raw/$component"/.coverage*
      touch "$CASE_ROOT/python-${component}-started"
      for attempt in {1..100}; do
        [[ -f "$CASE_ROOT/aggregate-started" ]] && break
        sleep 0.02
      done
      test -f "$CASE_ROOT/aggregate-started"''')
    uv.write_text(s)
    fake_bin = tmp_path / 'fake-bin'
    (fake_bin / 'ps').write_text('''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CASE_ROOT/ps.log"
# Model the real `ps ax -o pid= -o command=` shape only while the services are
# still live, so the post-combine cleanup flush can never recreate raw inputs.
if [[ ! -f "$CASE_ROOT/python-services-stopped" ]]; then
  printf '%s %s\\n' 424242 "$CASE_ROOT/.venv/bin/coverage run --parallel-mode --save-signal=USR1 backend"
fi
''')
    (fake_bin / 'kill').write_text('''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CASE_ROOT/kill.log"
if [[ "${1:-}" == "-USR1" ]]; then
  raw="$CASE_ROOT/singlebox/.dependencies/coverage/singlebox/raw"
  for component in backend baas; do
    mkdir -p "$raw/$component"
    printf '%s\\n' 'fake parallel coverage data' > "$raw/$component/.coverage.usr1save"
  done
  exit 0
fi
exec /bin/kill "$@"
''')
    # bash resolves `kill` as a builtin before PATH. Disable the builtin so the
    # fake receives the USR1 save signal; it forwards every other signal.
    (fake_bin / 'bashenv').write_text('enable -n kill\n')
    for name in ('ps', 'kill'):
        (fake_bin / name).chmod(0o755)
    launcher = tmp_path / 'singlebox/singlebox.sh'
    launcher_source = launcher.read_text().replace(
        '''  printf '%s\\n' '{}' > "${SINGLEBOX_COVERAGE_DIR}/backend/.coverage.fake"
  printf '%s\\n' '{}' > "${SINGLEBOX_COVERAGE_DIR}/baas/.coverage.fake"
''',
        '''  # BaaS persists parallel coverage only on USR1 and has no SIGTERM
  # handling, so this fixture starts with no raw `.coverage*` inputs.
''',
    )
    launcher.write_text(launcher_source.replace(
        "printf '%s\\n' \"$*\"",
        '''if [[ "$*" == "--standalone stop backend baas" ]]; then
  touch "$CASE_ROOT/python-services-stopped"
fi
printf '%s\\n' "$*"''',
    ))
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
for attempt in {1..500}; do
  [[ -f "$CASE_ROOT/python-backend-started" && -f "$CASE_ROOT/python-baas-started" ]] && break
  sleep 0.02
done
test -f "$CASE_ROOT/python-backend-started"
test -f "$CASE_ROOT/python-baas-started"
touch "$CASE_ROOT/aggregate-started"
echo aggregate >> "$CASE_ROOT/order"
cov_dir="${PWD}/apps/bcs/target/cov-e2e"''')
    e2e.write_text(s)
    return tmp_path


@pytest.mark.parametrize('companion_rc,shared_rc', [('0', '0'), ('17', '0'), ('0', '19')])
def test_overlap_join_before_aggregation_and_failure_status(pipeline, companion_rc, shared_rc):
    root = pipeline
    env = {**os.environ, 'PATH': str(root / 'fake-bin') + ':' + os.environ['PATH'],
           'BASH_ENV': str(root / 'fake-bin/bashenv'),
           'PYTHON': sys.executable, 'CASE_ROOT': str(root),
           'SINGLEBOX_STUB_LOG': str(root / 'singlebox.log'), 'UV_STUB_LOG': str(root / 'uv.log'),
           'COMPANION_RC': companion_rc, 'SHARED_RC': shared_rc}
    result = subprocess.run(['bash', 'singlebox/ci/singlebox_coverage.sh'], cwd=root,
                            env=env, capture_output=True, text=True, timeout=30)
    expected = int(shared_rc) or int(companion_rc)
    assert result.returncode == expected, result.stdout + result.stderr
    assert (root / 'order').read_text().splitlines() == ['aggregate']
    assert (root / 'python-backend-started').exists()
    assert (root / 'python-baas-started').exists()
    assert (root / 'aggregate-started').exists()
    assert (root / 'python-services-stopped').exists()
    raw = root / 'singlebox/.dependencies/coverage/singlebox/raw'
    for component in ('backend', 'baas'):
        # USR1-created inputs are consumed by combine and must not be recreated
        # by the post-combine cleanup flush.
        assert not list((raw / component).glob('.coverage*'))
    # The live writers must be signalled exactly once, while the fake `kill`
    # still recreates raw inputs; the post-combine cleanup flush must not.
    assert (root / 'kill.log').read_text().splitlines() == ['-USR1 424242']
    # One probe before the stop and one in final cleanup. A duplicate
    # post-combine flush is visible here even after `ps` stops matching.
    assert (root / 'ps.log').read_text().splitlines() == ['ax -o pid= -o command='] * 2
    reports = root / 'singlebox/.dependencies/coverage/singlebox/reports'
    assert (reports / 'bcs/companion-timings.json').exists()
    assert (reports / 'bcs/cobertura.xml').exists()
    phase_rows = {
        line.split('\t', 1)[0]
        for line in (reports / 'phase-timings.tsv').read_text().splitlines()[1:]
    }
    assert 'python_backend_report' in phase_rows
    assert 'python_baas_report' in phase_rows
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
           'BASH_ENV': str(root / 'fake-bin/bashenv'),
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


def test_parent_cancellation_reaps_python_reports_before_stack_cleanup(pipeline):
    import time

    root = pipeline
    uv = root / 'fake-bin/uv'
    source = uv.read_text()
    source = source.replace(
        'test -f "$CASE_ROOT/aggregate-started"',
        '''test -f "$CASE_ROOT/aggregate-started"
      for attempt in {1..400}; do
        [[ -f "$CASE_ROOT/allow-python-finish" ]] && break
        sleep 0.02
      done
      test -f "$CASE_ROOT/allow-python-finish"''',
        1,
    )
    source = source.replace(
        "printf '%s\\n' 'Name Stmts Miss Cover'",
        '''component="$(basename "$COVERAGE_FILE" .coverage)"
      touch "$CASE_ROOT/python-${component}-finished"
      printf '%s\\n' 'Name Stmts Miss Cover' ''',
        1,
    )
    uv.write_text(source)

    launcher = root / 'singlebox/singlebox.sh'
    launcher.write_text(launcher.read_text().replace(
        "printf '%s\\n' \"$*\"",
        '''if [[ "$*" == "--standalone stop all" ]]; then
  test -f "$CASE_ROOT/python-backend-finished" || exit 81
  test -f "$CASE_ROOT/python-baas-finished" || exit 82
  touch "$CASE_ROOT/cleanup-after-cancel"
fi
printf '%s\\n' "$*"''',
    ))

    env = {**os.environ, 'PATH': str(root / 'fake-bin') + ':' + os.environ['PATH'],
           'BASH_ENV': str(root / 'fake-bin/bashenv'),
           'PYTHON': sys.executable, 'CASE_ROOT': str(root),
           'SINGLEBOX_STUB_LOG': str(root / 'singlebox.log'),
           'UV_STUB_LOG': str(root / 'uv.log')}
    with (root / 'cancel-python.log').open('w') as log:
        process = subprocess.Popen(
            ['bash', 'singlebox/ci/singlebox_coverage.sh'],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while not (root / 'aggregate-started').exists():
                assert process.poll() is None, (root / 'cancel-python.log').read_text()
                assert time.monotonic() < deadline
                time.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            time.sleep(1.0)
            # The reports are gated on allow-python-finish; cleanup must not
            # reach `stop all` while they are still writing.
            assert not (root / 'python-backend-finished').exists()
            assert not (root / 'python-baas-finished').exists()
            (root / 'allow-python-finish').touch()
            assert process.wait(timeout=15) == 143
            assert (root / 'cleanup-after-cancel').exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
