"""Environment handling for a task running inside a local Bot."""
from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Mapping


class LocalRunnerEnvironment:
    def __init__(self, env: Mapping[str, str]):
        self.env = env

    def startup_environment(self) -> dict[str, str]:
        # These are resolved on the selected Bot, never on the CW server.
        state = self.env.get("OPENCLAW_STATE_DIR", "").strip()
        workspace = (self.env.get("OPENCLAW_WORKSPACE_DIR") or self.env.get("OPENCLAW_WORKSPACE", "")).strip()
        if not state or not workspace:
            raise ValueError("local Runner requires OPENCLAW_STATE_DIR and OPENCLAW_WORKSPACE_DIR/OPENCLAW_WORKSPACE")
        state_path, workspace_path = Path(state).expanduser().resolve(strict=True), Path(workspace).expanduser().resolve(strict=True)
        if not state_path.is_dir() or not workspace_path.is_dir():
            raise ValueError("local Runner paths must be directories")
        workspace_path.relative_to(state_path)
        return {"OPENCLAW_STATE_DIR": str(state_path), "OPENCLAW_HOME": str(state_path),
                "OPENCLAW_CONFIG_PATH": str(state_path / "openclaw.json"),
                "OPENCLAW_WORKSPACE": str(workspace_path), "ENGINE_RUNTIME_LAYOUT_HOME": str(state_path.parent),
                "SECBAAS_SANDBOX_BACKEND": "local_proc"}

    def validate_user(self, user: str) -> None:
        # A local Bot runs as its existing OS user; it does not acquire privileges.
        return None

    def allow_loopback_download(self) -> bool:
        return True

    def state_root(self, default: Path) -> Path:
        return self._directory("OPENCLAW_STATE_DIR")

    def workspace(self, default: Path) -> Path:
        workspace = self._directory("OPENCLAW_WORKSPACE")
        workspace.relative_to(self._directory("OPENCLAW_STATE_DIR"))
        return workspace

    def _directory(self, name: str) -> Path:
        value = self.env.get(name, "").strip()
        if not value:
            raise RuntimeError(f"local_proc requires {name}")
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise RuntimeError(f"local_proc {name} is not a directory")
        return path

    def validate_agent_store(self, base: Path, agent_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", agent_id) or ".." in agent_id:
            raise RuntimeError("Invalid local agent ID")
        for candidate in (base / agent_id, base / agent_id.replace(":", "-").lower()):
            candidate.resolve().relative_to(self._directory("OPENCLAW_STATE_DIR"))

    def resolve_workspace(self, explicit, default, resolve_path):
        return resolve_path(explicit or default)

    def deployment_workspace(self, explicit, resolved):
        return str(resolved)

    def bench_environment(self, workspace: Path) -> dict[str, str]:
        return dict(self.env) | {"OPENCLAW_WORKSPACE": str(workspace)}

    def allows_untracked_cleanup(self) -> bool:
        return False

    def agent_cleanup_command(self, openclaw: str, agent_id: str) -> list[str]:
        if self.env.get("CLAWWEB_VERSION") == "openversion":
            return [openclaw, "agents", "delete", agent_id, "--force", "--json"]
        return [openclaw, "gateway", "call", "agents.delete", "--params",
                json.dumps({"agentId": agent_id, "deleteFiles": False}), "--json"]

    def prepare_task(self, arguments: list[str], script_directory: Path) -> int:
        logfile = Path(arguments[arguments.index("--log-file") + 1])
        with logfile.open("a", encoding="utf-8") as output:
            output.write("local_proc runtime is managed by LocalProcessManager; skip container environment adaptation\n")
        return 0
