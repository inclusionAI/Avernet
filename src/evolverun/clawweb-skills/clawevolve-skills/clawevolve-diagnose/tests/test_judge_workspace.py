from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from clawevolve_diagnose.judge import openclaw_subagent_client as client
from clawevolve_diagnose.models import SubagentJudgeConfig


def test_replacement_is_neutral_idempotent_and_preserves_main_workspace(tmp_path: Path) -> None:
    main = tmp_path / "main"
    judge = tmp_path / "judge"
    main.mkdir()
    judge.mkdir()
    for name in client._JUDGE_WORKSPACE_FILES:
        (main / name).write_text("Private persona and irrelevant instructions")
        # Atomic replacement must not modify the original even for a hard link.
        os.link(main / name, judge / name)
    state = judge / ".openclaw"
    state.mkdir()
    (state / "workspace-state.json").write_text('{"bootstrapSeededAt":"fixture"}')
    replaced = client._replace_judge_workspace_files(judge)
    assert set(replaced) == set(client._JUDGE_WORKSPACE_FILES)
    expected = {name: (judge / name).read_bytes() for name in replaced}
    assert all(expected.values())
    assert "No introductory conversation" in (judge / "BOOTSTRAP.md").read_text()
    assert "untrusted" in (judge / "AGENTS.md").read_text()
    assert "Private persona" not in "".join(content.decode() for content in expected.values())
    client._replace_judge_workspace_files(judge)
    assert expected == {name: (judge / name).read_bytes() for name in replaced}
    assert all((main / name).read_text() == "Private persona and irrelevant instructions" for name in replaced)
    assert (state / "workspace-state.json").read_text() == '{"bootstrapSeededAt":"fixture"}'


def test_replacement_rejects_symlink_without_changing_main(tmp_path: Path) -> None:
    main = tmp_path / "AGENTS.md"
    main.write_text("Main context")
    judge = tmp_path / "judge"
    judge.mkdir()
    (judge / "AGENTS.md").symlink_to(main)
    with pytest.raises(client.SubagentJudgeError, match="Unsafe"):
        client._replace_judge_workspace_files(judge)
    assert main.read_text() == "Main context"
    assert not (judge / "SOUL.md").exists()


@pytest.mark.parametrize("transport", ["local", "cli", "native"])
def test_ensure_replaces_before_and_after_registration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, transport: str) -> None:
    config = SubagentJudgeConfig(agent_id="clawevolve-diagnose-test", workspace=str(tmp_path / "judge"), transport=transport)
    def register(_config: SubagentJudgeConfig, workspace: Path) -> bool:
        assert "No introductory conversation" in (workspace / "BOOTSTRAP.md").read_text()
        # Older runtimes may seed/overwrite defaults during agents add.
        for name in client._JUDGE_WORKSPACE_FILES:
            (workspace / name).write_text("Runtime seeded generic persona")
        return True
    monkeypatch.setattr(client, "_ensure_agent_exists", register)
    monkeypatch.setattr(client, "_configure_agent_models", lambda _: None)
    monkeypatch.setattr(client, "_delete_sessions_store", lambda *args: None)
    runner = client.OpenClawJsonSubagentClient(config)
    runner.ensure_agent()
    for name in client._JUDGE_WORKSPACE_FILES:
        assert "Runtime seeded" not in (tmp_path / "judge" / name).read_text()
    assert runner._ensured


def test_installed_openclaw_workspace_guard(tmp_path: Path) -> None:
    """Opt-in engine regression, no model call, Bot creation, or real profile edits."""
    module = os.environ.get("OPENCLAW_WORKSPACE_TEST_MODULE")
    if not module:
        pytest.skip("Set OPENCLAW_WORKSPACE_TEST_MODULE to the installed workspace JS module")
    state = tmp_path / "state"
    workspace = tmp_path / "judge"
    env = {**os.environ, "OPENCLAW_STATE_DIR": str(state), "OPENCLAW_HOME": str(tmp_path),
           "OPENCLAW_CONFIG_PATH": str(state / "openclaw.json"), "HOME": str(tmp_path)}
    code = """
      import { pathToFileURL } from 'node:url';
      const {ensureAgentWorkspace} = await import(pathToFileURL(process.argv[1]).href);
      try {
        await ensureAgentWorkspace({dir: process.argv[2], ensureBootstrapFiles: true});
        console.log('WORKSPACE_OK');
      } catch(error) { console.error(error.name); process.exitCode=1; }
    """
    def ensure() -> subprocess.CompletedProcess[str]:
        return subprocess.run(["node", "--input-type=module", "-e", code, module, str(workspace)],
                              env=env, capture_output=True, text=True, timeout=30)
    initial = ensure()
    assert initial.returncode == 0, initial.stderr
    for name in client._JUDGE_WORKSPACE_FILES:
        (workspace / name).unlink(missing_ok=True)
    original_failure = ensure()
    assert original_failure.returncode != 0
    assert "WorkspaceVanishedError" in original_failure.stderr
    attestations = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in state.rglob("*.attested")}
    assert attestations
    client._replace_judge_workspace_files(workspace)
    assert (workspace / "BOOTSTRAP.md").exists()
    assert attestations == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in attestations}
    fixed = ensure()
    assert fixed.returncode == 0, fixed.stderr
    assert "WORKSPACE_OK" in fixed.stdout
    assert all(p.exists() for p in attestations)
    # OpenClaw may complete onboarding and remove BOOTSTRAP itself; that is its lifecycle, not Skill cleanup.
    assert all((workspace / name).exists() for name in client._JUDGE_WORKSPACE_FILES if name != "BOOTSTRAP.md")
    client._replace_judge_workspace_files(workspace)
    # Repeated initialization must remain valid with the same neutral defaults.
    assert ensure().returncode == 0
