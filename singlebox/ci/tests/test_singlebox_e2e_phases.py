"""Execute the real BCS runner against isolated command fixtures."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def phase_repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    scripts = tmp_path / 'apps/bcs/scripts'
    (scripts / 'e2e-test').mkdir(parents=True)
    shutil.copy(ROOT / 'apps/bcs/scripts/e2e_coverage.sh', scripts)
    (scripts / 'e2e-test/mock_services.sh').write_text('bcs_e2e_mock_stop() { :; }\n')
    tools = tmp_path / 'bin'
    tools.mkdir()
    events = tmp_path / 'events'
    for file, text in {
        'e2e-test/e2e.sh': 'echo stories >> "$EVENTS"; echo hit >> "$BCS_LOG"; exit "${STORIES_RC:-0}"',
        'caller_e2e_coverage.sh': 'echo caller >> "$EVENTS"; exit "${CALLER_RC:-0}"',
        'oauth_e2e_coverage.sh': 'echo oauth >> "$EVENTS"; exit "${OAUTH_RC:-0}"',
    }.items():
        (scripts / file).write_text(text + '\n')
    for file in ('e2e_cov_gate.py', 'adapters_endpoint_coverage.py', 'cli_command_coverage.py'):
        (scripts / file).write_text(
            'import os,sys\nfrom pathlib import Path\n'
            'with Path(os.environ["EVENTS"]).open("a") as f: f.write(Path(__file__).name+"\\n")\n'
            'assert "hit" in Path(os.environ["BCS_LOG"]).read_text()\n'
            'sys.exit(int(os.environ.get("GATE_RC", "0")))\n'
        )
    singlebox = tmp_path / 'singlebox'
    singlebox.mkdir()
    (singlebox / 'singlebox.sh').write_text('#!/bin/bash\necho stop >> "$EVENTS"\n')
    (singlebox / 'singlebox.sh').chmod(0o755)
    cov = tmp_path / 'apps/bcs/target/cov-e2e'
    (cov / 'llvm-cov-target/debug').mkdir(parents=True)
    cli = cov / 'llvm-cov-target/debug/bcs-cli'
    cli.write_text('#!/bin/sh\nexit 0\n')
    cli.chmod(0o755)
    for file, text in {
        'lsof': 'exit 1',
        'cargo': 'echo report >> "$EVENTS"; test -f "$PROFILE" || exit 22; echo TOTAL',
    }.items():
        (tools / file).write_text('#!/bin/sh\n' + text + '\n')
        (tools / file).chmod(0o755)
    profile = cov / 'llvm-cov-target/bcs-current.profraw'
    profile.write_text('current')
    (cov / 'cli_commands.log').write_text('cli-hit\n')
    bcs_log = tmp_path / 'bcs.log'
    bcs_log.write_text('previous-hit\n')
    env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'],
           'EVENTS': str(events), 'BCS_LOG': str(bcs_log), 'PROFILE': str(profile)}
    for key in ('BCS_E2E_MOCK_BASE_URL', 'BCS_E2E_CHANGED_BASE_REF', 'BCS_E2E_CHANGED_LINE_MIN'):
        env.pop(key, None)
    return tmp_path, env, events, profile


def run(repo, *args, **overrides):
    path, env, events, profile = repo
    result = subprocess.run(['bash', 'apps/bcs/scripts/e2e_coverage.sh', *args],
                            cwd=path, env={**env, **overrides}, capture_output=True, text=True)
    lines = events.read_text().splitlines() if events.exists() else []
    return result, lines


def test_shared_phase_keeps_server_and_profiles_for_later_report(phase_repo):
    result, events = run(phase_repo, '--skip-start', '--shared-tests-only', STORIES_RC='7')
    assert result.returncode == 7, result.stderr
    assert events == ['stories']
    assert phase_repo[3].exists()


def test_aggregate_phase_preserves_evidence_runs_all_gates_and_cleans_profiles(phase_repo):
    result, events = run(phase_repo, '--skip-start', '--aggregate-only', GATE_RC='9')
    assert result.returncode == 9, result.stderr
    assert 'stories' not in events and 'caller' not in events and 'oauth' not in events
    assert events.count('report') == 3
    assert 'e2e_cov_gate.py' in events and 'adapters_endpoint_coverage.py' in events
    assert 'cli_command_coverage.py' in events
    assert not phase_repo[3].exists()
    assert (phase_repo[0] / 'bcs.log').read_text() == 'previous-hit\n'


def test_default_still_runs_all_tests_and_reports_after_failure(phase_repo):
    result, events = run(phase_repo, '--skip-start', CALLER_RC='8')
    assert result.returncode == 8, result.stderr
    assert events[:3] == ['stories', 'caller', 'oauth']
    assert events.count('report') == 3


@pytest.mark.parametrize('args', [
    ['--shared-tests-only'], ['--aggregate-only'],
    ['--skip-start', '--shared-tests-only', '--aggregate-only'],
    ['--skip-start', '--aggregate-only', '--no-stop'],
])
def test_rejects_unsafe_phase_combinations_before_touching_services(phase_repo, args):
    result, events = run(phase_repo, *args)
    assert result.returncode == 2
    assert events == []
