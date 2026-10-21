import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

from agentclaw.community.core.bot_management.engines.aicoding import artifact_command as module

class LocalExecutor:
    def exec_shell_new(self, *, device_id, shell_cmd, timeout_seconds):
        assert len(shell_cmd) < 8192
        assert 0 < timeout_seconds <= 300
        result = subprocess.run(shell_cmd, shell=True, capture_output=True, text=True, timeout=timeout_seconds)
        return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout)

def test_large_script_waits_for_success(tmp_path):
    output = tmp_path / 'done'
    command = '#' + 'x' * 20000 + '\n' + f'touch {output}'
    module.run_artifact_command(LocalExecutor(), 'test', command)
    assert output.exists()

def test_failure_is_not_success():
    with pytest.raises(module.ArtifactCommandError):
        module.run_artifact_command(LocalExecutor(), 'test', 'exit 7')

def test_deadline_cancels_writer(tmp_path):
    output = tmp_path / 'must-not-exist'
    with pytest.raises(TimeoutError):
        module.run_artifact_command(LocalExecutor(), 'test', f'sleep 5; touch {output}', timeout_seconds=0.5)
    assert not output.exists()
