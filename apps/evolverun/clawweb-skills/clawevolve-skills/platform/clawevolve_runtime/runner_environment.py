"""Runner-side composition root and small interface for execution environments.

The Host may explicitly select local/container. Older releases keep their
existing local_proc signal. Business handlers do not select implementations.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import shlex
import sys
from typing import Mapping, Protocol
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RunnerEnvironment(Protocol):
    def startup_environment(self) -> dict[str, str]: ...
    def validate_user(self, user: str) -> None: ...
    def allow_loopback_download(self) -> bool: ...
    def prepare_task(self, arguments: list[str], script_directory: Path) -> int: ...
    def state_root(self, default: Path) -> Path: ...
    def workspace(self, default: Path) -> Path: ...
    def validate_agent_store(self, base: Path, agent_id: str) -> None: ...
    def resolve_workspace(self, explicit: str, default: Path, resolve_path: Callable) -> Path: ...
    def deployment_workspace(self, explicit: str, resolved: Path) -> str | Path: ...
    def bench_environment(self, workspace: Path) -> dict[str, str] | None: ...
    def allows_untracked_cleanup(self) -> bool: ...
    def agent_cleanup_command(self, openclaw: str, agent_id: str) -> list[str]: ...


def resolve_runner_environment(env: Mapping[str, str] | None = None) -> RunnerEnvironment:
    source = dict(os.environ if env is None else env)
    name = source.get("CLAWEVOLVE_RUNNER_ENVIRONMENT")
    if name is None:
        name = "local" if source.get("SECBAAS_SANDBOX_BACKEND") == "local_proc" else "container"
    if name == "local":
        from clawevolve_runtime.local_runner_environment import LocalRunnerEnvironment
        return LocalRunnerEnvironment(source)
    if name == "container":
        from clawevolve_runtime.container_runner_environment import ContainerRunnerEnvironment
        return ContainerRunnerEnvironment(source)
    raise ValueError("unknown Runner environment")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("shell-init", "prepare-task"))
    parser.add_argument("--check-user", action="store_true")
    parser.add_argument("--script-directory", type=Path)
    args, remaining = parser.parse_known_args(argv)
    if remaining[:1] == ["--"]:
        remaining = remaining[1:]
    environment = resolve_runner_environment()
    if args.action == "shell-init":
        if args.check_user:
            # Preserve the existing root -> admin rule in both environments.
            if os.getuid() == 0:
                if not remaining:
                    raise ValueError("Runner command is required")
                print("exec " + shlex.join(["runuser", "-u", "admin", "--", "bash", *remaining]))
                return 0
            environment.validate_user(pwd.getpwuid(os.getuid()).pw_name)
        print("\n".join(f"export {key}={shlex.quote(value)}" for key, value in environment.startup_environment().items()))
        return 0
    if args.script_directory is None:
        raise ValueError("script directory is required")
    return environment.prepare_task(remaining, args.script_directory)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
