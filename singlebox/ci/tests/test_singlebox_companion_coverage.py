"""Verify build ordering, process lifetime, evidence isolation and failure results."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[3]
SUPERVISOR = ROOT / 'apps/bcs/scripts/ci/companion_coverage.py'
BUILD = ROOT / 'apps/bcs/scripts/build_e2e_companions.sh'


def wait_for(path, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        assert process.poll() is None, process.communicate()
        time.sleep(0.02)
    raise AssertionError(f'timed out waiting for {path}')


def wait_for_children_to_stop(pids, timeout=5):
    """Allow SIGKILL-pending children to leave transient kernel wait states."""
    deadline = time.monotonic() + timeout
    running = {}
    while time.monotonic() < deadline:
        running = {}
        for pid in pids:
            try:
                state = Path(f'/proc/{pid}/stat').read_text().split()[2]
            except FileNotFoundError:
                continue
            if state not in {'X', 'Z'}:
                running[pid] = state
        if not running:
            return
        time.sleep(0.02)
    raise AssertionError(f'children still running: {running}')


@pytest.fixture
def companion_repo(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    # The real builder is used: only the external Cargo invocation is a stand-in.
    if BUILD.exists():
        shutil.copy(BUILD, scripts)
    tools = tmp_path / 'tools'
    tools.mkdir()
    cargo = tools / 'cargo'
    cargo.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
root = pathlib.Path(os.environ['CASE_ROOT'])
(root / 'build.json').write_text(json.dumps({{'args':sys.argv[1:], 'flags':os.environ.get('RUSTFLAGS'), 'target':os.environ.get('CARGO_TARGET_DIR')}}))
sys.exit(int(os.environ.get('BUILD_RC','0')))
''')
    cargo.chmod(0o755)
    for name in ('caller', 'oauth'):
        (scripts / f'{name}_e2e_coverage.sh').write_text(f'''#!/bin/bash
set -eu
test "$1" = --run-only
test -f "$CASE_ROOT/build.json"
touch "$CASE_ROOT/{name}-started"
if [[ "${{HOLD:-0}}" == 1 ]]; then
  sleep 300 &
  echo $! > "$CASE_ROOT/{name}-child"
  wait
else
  # A barrier makes this fail if the supervisor serializes Caller and OAuth.
  for attempt in {{1..100}}; do
    [[ -f "$CASE_ROOT/caller-started" && -f "$CASE_ROOT/oauth-started" ]] && break
    sleep 0.02
  done
  test -f "$CASE_ROOT/caller-started"
  test -f "$CASE_ROOT/oauth-started"
  touch "$CASE_ROOT/{name}-finished"
  exit "${{{name.upper()}_RC:-0}}"
fi
''')
    profiles = tmp_path / 'target/cov-e2e/llvm-cov-target'
    profiles.mkdir(parents=True)
    (profiles / 'bcs-shared.profraw').write_text('do not remove')
    (profiles.parent / 'oauth_e2e.json').write_text('{"status":"passed"}')
    env = {**os.environ, 'CASE_ROOT': str(tmp_path),
           'PATH': str(tools) + ':' + os.environ['PATH']}
    return tmp_path, env


def command(root):
    return [sys.executable, str(SUPERVISOR), '--bcs-dir', str(root),
            '--report-dir', str(root / 'reports')]


@pytest.mark.parametrize('caller_rc,oauth_rc', [('0', '0'), ('17', '0'), ('0', '19')])
def test_builds_both_bins_once_then_overlaps_and_joins_all_runs(companion_repo, caller_rc, oauth_rc):
    root, env = companion_repo
    result = subprocess.run(command(root), env={**env, 'CALLER_RC': caller_rc, 'OAUTH_RC': oauth_rc},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == (int(caller_rc) or int(oauth_rc)), result.stderr
    build = json.loads((root / 'build.json').read_text())
    assert build['args'] == ['build', '-p', 'bcs', '--features', 'test-utils',
                             '--bin', 'bcs-caller-e2e', '--bin', 'bcs-oauth-e2e']
    assert build['flags'] == '-Cinstrument-coverage -Clink-dead-code'
    assert build['target'].endswith('target/cov-e2e/llvm-cov-target')
    assert (root / 'caller-finished').exists() and (root / 'oauth-finished').exists()
    timings = json.loads((root / 'reports/companion-timings.json').read_text())
    assert timings['phases']['caller']['exit_code'] == int(caller_rc)
    assert timings['phases']['oauth']['exit_code'] == int(oauth_rc)
    assert timings['status'] == ('passed' if result.returncode == 0 else 'failed')
    assert (root / 'target/cov-e2e/llvm-cov-target/bcs-shared.profraw').read_text() == 'do not remove'


def test_failed_build_invalidates_prior_success_and_does_not_start_tests(companion_repo):
    root, env = companion_repo
    result = subprocess.run(command(root), env={**env, 'BUILD_RC': '23'}, capture_output=True, timeout=15)
    assert result.returncode == 23, result.stderr
    assert json.loads((root / 'target/cov-e2e/oauth_e2e.json').read_text())['status'] == 'failed'
    assert not (root / 'caller-started').exists()
    assert not (root / 'oauth-started').exists()


def test_sigterm_stops_owned_descendants_and_records_failure(companion_repo):
    root, env = companion_repo
    process = subprocess.Popen(command(root), env={**env, 'HOLD': '1'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for(root / 'caller-child', process)
        wait_for(root / 'oauth-child', process)
        children = [int((root / f'{name}-child').read_text()) for name in ('caller', 'oauth')]
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=15)
        assert process.returncode != 0
        # A killed child can remain in uninterruptible kernel wait (D) before
        # it reaches a terminal state; do not sample /proc only once.
        wait_for_children_to_stop(children)
        assert json.loads((root / 'reports/companion-timings.json').read_text())['status'] == 'failed'
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize('name', ['caller', 'oauth'])
def test_run_only_wrappers_use_prebuilt_bins_without_touching_shared_evidence(tmp_path, name):
    scripts = tmp_path / 'scripts'
    (scripts / 'e2e-test/oauth').mkdir(parents=True)
    shutil.copy(ROOT / f'apps/bcs/scripts/{name}_e2e_coverage.sh', scripts)
    shutil.copy(BUILD, scripts)
    runtime = tmp_path / 'target/cov-e2e/llvm-cov-target'
    (runtime / 'debug').mkdir(parents=True)
    binary = runtime / f'debug/bcs-{name}-e2e'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    shared = runtime / 'bcs-shared.profraw'
    shared.write_text('shared')
    names = (['caller_execution_story.py', 'task_cache_story.py', 'custom_binding_story.py']
             if name == 'caller' else ['oauth/story.py'])
    events = tmp_path / 'events'
    for story in names:
        (scripts / 'e2e-test' / story).write_text(
            'import os\nfrom pathlib import Path\n'
            'with Path(os.environ["EVENTS"]).open("a") as f: f.write(Path(__file__).name+"\\n")\n'
            'assert "%m-%p.profraw" in os.environ["LLVM_PROFILE_FILE"]\n'
        )
    tools = tmp_path / 'tools'
    tools.mkdir()
    cargo = tools / 'cargo'
    cargo.write_text('#!/bin/sh\nexit 99\n')
    cargo.chmod(0o755)
    env = {**os.environ, 'EVENTS': str(events), 'PATH': str(tools) + ':' + os.environ['PATH']}
    result = subprocess.run(['bash', str(scripts / f'{name}_e2e_coverage.sh'), '--run-only'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == [Path(n).name for n in names]
    assert shared.read_text() == 'shared'
    binary.unlink()
    events.unlink()
    result = subprocess.run(['bash', str(scripts / f'{name}_e2e_coverage.sh'), '--run-only'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert not events.exists()
