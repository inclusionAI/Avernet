"""The standalone story owns children even when construction never returns."""
import importlib.util
from pathlib import Path
import queue
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('worker_story', ROOT / 'apps/bcs/scripts/e2e-test/worker_runtime_story.py')
STORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORY)


@pytest.mark.parametrize('readiness', ['malformed', 'wrong', 'silent'])
def test_failed_readiness_reaps_owned_process(tmp_path, monkeypatch, readiness):
    child = tmp_path / 'child'
    output = {'malformed': "print('not-json', flush=True)",
              'wrong': "print('{}', flush=True)", 'silent': 'pass'}[readiness]
    child.write_text(f'#!{sys.executable}\nimport time\n{output}\ntime.sleep(300)\n')
    child.chmod(0o755)
    children = []
    popen = subprocess.Popen

    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(STORY.subprocess, 'Popen', start)

    def bounded_read(self):
        item = self.replies.get(timeout=0.25)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(STORY.Host, 'read', bounded_read)
    with (tmp_path / 'child.log').open('w') as log:
        with pytest.raises((ValueError, AssertionError, queue.Empty)):
            STORY.Host(child, tmp_path / 'archive.db', log)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


def test_profile_cleanup_failure_invalidates_previous_success(tmp_path):
    import json
    import shutil
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    wrapper = scripts / 'worker_e2e_coverage.sh'
    shutil.copy(ROOT / 'apps/bcs/scripts/worker_e2e_coverage.sh', wrapper)
    runtime = tmp_path / 'target/cov-e2e/llvm-cov-target'
    runtime.mkdir(parents=True)
    # A directory matching the profile glob forces unlink to fail deterministically.
    (runtime / 'worker-stale.profraw').mkdir()
    report = runtime.parent / 'worker_e2e.json'
    report.write_text('{"status":"passed"}')
    result = subprocess.run(['bash', str(wrapper), '--run-only'], capture_output=True, timeout=10)
    assert result.returncode != 0
    assert json.loads(report.read_text())['status'] == 'failed'
