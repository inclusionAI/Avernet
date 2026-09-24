"""Existing container environment requirements for the shared Runner."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Mapping


class ContainerRunnerEnvironment:
    def __init__(self, env: Mapping[str, str]):
        self.env = env

    def startup_environment(self) -> dict[str, str]:
        workspace = self.env.get("OPENCLAW_WORKSPACE") or "/home/admin/.openclaw/workspace"
        return {"OPENCLAW_WORKSPACE": workspace,
                "OPENCLAW_HOME": self.env.get("OPENCLAW_HOME") or str(Path(workspace).parent)}

    def validate_user(self, user: str) -> None:
        if user != "admin":
            raise ValueError("runner must execute as admin")

    def allow_loopback_download(self) -> bool:
        return False

    def state_root(self, default: Path) -> Path:
        return default

    def workspace(self, default: Path) -> Path:
        return default

    def validate_agent_store(self, base: Path, agent_id: str) -> None:
        return None

    def resolve_workspace(self, explicit, default, resolve_path):
        return Path(explicit).expanduser() if explicit else default

    def deployment_workspace(self, explicit, resolved):
        return explicit or resolved

    def bench_environment(self, workspace: Path) -> None:
        return None

    def allows_untracked_cleanup(self) -> bool:
        return True

    def agent_cleanup_command(self, openclaw: str, agent_id: str) -> list[str]:
        return [openclaw, "agents", "delete", agent_id, "--force", "--json"]

    def prepare_task(self, arguments: list[str], script_directory: Path) -> int:
        # Preserve the existing shell maintenance implementation and its locks.
        script = Path(__file__).with_name("container_runner_environment.sh")
        names = {"--task-id": "TASK_ID", "--step-id": "STEP_ID", "--log-file": "LOG_FILE",
                 "--runtime-maintenance": "RUNTIME_MAINTENANCE",
                 "--preflight-active-evolve-guard": "PREFLIGHT_ACTIVE_EVOLVE_GUARD",
                 "--refresh-gateway-before-handler": "REFRESH_GATEWAY_BEFORE_HANDLER"}
        values = dict(zip(arguments[::2], arguments[1::2]))
        if len(arguments) != len(names) * 2 or set(values) != set(names):
            raise ValueError("invalid environment preparation arguments")
        env = dict(self.env) | {names[key]: value for key, value in values.items()}
        env.update(SCRIPT_DIR=str(script_directory), GATEWAY_RESTARTED_FOR_TASK="false")
        return subprocess.run(["bash", str(script)], env=env, check=False).returncode
