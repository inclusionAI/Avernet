"""Select cleanup without giving a local Agent ownership of task files."""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "platform"))
from clawevolve_runtime.runner_environment import resolve_runner_environment



def agent_cleanup_command(
    openclaw_path: str, agent_id: str, env: dict[str, str]
) -> list[str]:
    return resolve_runner_environment(env).agent_cleanup_command(openclaw_path, agent_id)
