from pathlib import Path
import os
import subprocess

import pytest

from clawevolve_plan.discovery.agent import (
    DiscoveryAgentError, _OPENCLAW_BOOTSTRAP_FILES,
    _snapshot_workspace_bootstrap, _cleanup_workspace_bootstrap, prepare_plan_workspace,
)


def test_contract_registration_preserves_neutral_context_and_main_files(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    contract = tmp_path / "contract_agent"
    contract.mkdir()
    for name in _OPENCLAW_BOOTSTRAP_FILES:
        (main / name).write_text("Main Bot persona")
        os.link(main / name, contract / name)
    prepare_plan_workspace(contract)
    before = _snapshot_workspace_bootstrap(contract)
    for name in _OPENCLAW_BOOTSTRAP_FILES:
        (contract / name).write_text("Generated generic assistant context")
    cleaned = _cleanup_workspace_bootstrap(contract, before, restore_existing=True)
    assert not any(item.startswith("removed-new:") for item in cleaned)
    for name in _OPENCLAW_BOOTSTRAP_FILES:
        assert (contract / name).read_bytes() == before[name]["content"]
        assert (main / name).read_text() == "Main Bot persona"
    prepare_plan_workspace(contract)
    assert "No introductory conversation" in (contract / "BOOTSTRAP.md").read_text()


def test_contract_context_symlink_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("main")
    contract = tmp_path / "contract"
    contract.mkdir()
    (contract / "AGENTS.md").symlink_to(source)
    with pytest.raises(DiscoveryAgentError, match="Unsafe"):
        prepare_plan_workspace(contract)
    assert source.read_text() == "main"


def test_installed_openclaw_contract_workspace(tmp_path: Path) -> None:
    module = os.environ.get("OPENCLAW_WORKSPACE_TEST_MODULE")
    if not module:
        pytest.skip("Optional installed OpenClaw initialization regression")
    workspace = tmp_path / "contract_agent"
    workspace.mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "OPENCLAW_HOME": str(tmp_path),
           "OPENCLAW_STATE_DIR": str(tmp_path / "state"),
           "OPENCLAW_CONFIG_PATH": str(tmp_path / "state/openclaw.json")}
    code = """
      import {pathToFileURL} from 'node:url';
      const {ensureAgentWorkspace} = await import(pathToFileURL(process.argv[1]).href);
      await ensureAgentWorkspace({dir:process.argv[2], ensureBootstrapFiles:true});
    """
    def ensure():
        return subprocess.run(["node", "--input-type=module", "-e", code, module, str(workspace)],
                              env=env, capture_output=True, text=True, timeout=30)
    old_snapshot = _snapshot_workspace_bootstrap(workspace)
    assert ensure().returncode == 0
    _cleanup_workspace_bootstrap(workspace, old_snapshot, restore_existing=True)
    failure = ensure()
    assert failure.returncode != 0 and "WorkspaceVanishedError" in failure.stderr
    # Recover same previously attested directory without deleting attestation records.
    prepare_plan_workspace(workspace)
    fixed_snapshot = _snapshot_workspace_bootstrap(workspace)
    assert ensure().returncode == 0
    _cleanup_workspace_bootstrap(workspace, fixed_snapshot, restore_existing=True)
    result = ensure()
    assert result.returncode == 0, result.stderr
