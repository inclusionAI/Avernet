#!/usr/bin/env python3
"""Run the BCN plugin compatibility probe against one exact OpenClaw version."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO


EXPECTED_REPLY = "OPENCLAW_COMPAT_OK"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_plugin_dir(repo_root: Path) -> Path:
    return repo_root / "src/bcs/crates/plugins/openclaw-channel-bcn"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def terminate_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_logged(
    command: list[str],
    *,
    log_file: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as log:
        log.write(f"command: {' '.join(command)}\n")
        log.flush()
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
        )


def wait_for_json(path: Path, *, timeout: float, process: subprocess.Popen[Any] | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            try:
                return read_json(path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                last_error = error
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"process exited with code {process.returncode} before writing {path}")
        time.sleep(0.1)
    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"timed out waiting for {path}{detail}")


def ensure_plugin_build(
    plugin_dir: Path,
    output_dir: Path,
    *,
    skip_build: bool,
) -> dict[str, Any]:
    node_modules = plugin_dir / "node_modules"
    dist_entry = plugin_dir / "dist/esm/index.js"
    install_log = output_dir / "plugin-install.log"
    build_log = output_dir / "plugin-build.log"

    required_tools = [node_modules / ".bin/tsc", node_modules / ".bin/tshy"]
    if not all(tool.is_file() for tool in required_tools):
        completed = run_logged(
            ["npm", "install", "--package-lock=false", "--no-audit", "--no-fund"],
            cwd=plugin_dir,
            log_file=install_log,
            timeout=600,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"plugin dependency install failed; see {install_log}")

    if not skip_build:
        completed = run_logged(
            ["npm", "run", "build"],
            cwd=plugin_dir,
            log_file=build_log,
            timeout=300,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"plugin build failed; see {build_log}")

    if not dist_entry.is_file():
        raise RuntimeError(f"plugin build output is missing: {dist_entry}")

    tsc = node_modules / ".bin/tsc"
    if not tsc.is_file():
        raise RuntimeError(f"TypeScript compiler missing after plugin setup: {tsc}")
    return {"dist_entry": str(dist_entry), "typescript": str(tsc)}


def install_openclaw(
    version: str,
    install_dir: Path,
    output_dir: Path,
    npm_cache: Path | None,
) -> tuple[Path, dict[str, Any]]:
    install_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        install_dir / "package.json",
        {"name": "openclaw-compat-host", "private": True, "type": "module"},
    )
    command = [
        "npm",
        "install",
        "--no-save",
        "--package-lock=false",
        "--no-audit",
        "--no-fund",
        f"openclaw@{version}",
        "ws@8.18.3",
        "@types/ws@8.5.13",
        "@types/node@20",
    ]
    env = os.environ.copy()
    if npm_cache:
        npm_cache.mkdir(parents=True, exist_ok=True)
        env["npm_config_cache"] = str(npm_cache)
    log_file = output_dir / "openclaw-install.log"
    completed = run_logged(command, cwd=install_dir, env=env, log_file=log_file, timeout=900)
    if completed.returncode != 0:
        raise RuntimeError(f"OpenClaw {version} install failed; see {log_file}")

    package_root = install_dir / "node_modules/openclaw"
    package = read_json(package_root / "package.json")
    resolved = package.get("version")
    if resolved != version:
        raise RuntimeError(f"requested OpenClaw {version}, npm installed {resolved}")
    executable = install_dir / "node_modules/.bin/openclaw"
    if not executable.is_file():
        raise RuntimeError(f"OpenClaw executable missing after install: {executable}")
    return executable, package


def run_sdk_probe(
    *,
    plugin_dir: Path,
    package_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output = output_dir / "sdk-probe.json"
    log_file = output_dir / "sdk-probe.log"
    completed = run_logged(
        [
            "node",
            str(plugin_dir / "test/compat/sdk_probe.mjs"),
            "--package-root",
            str(package_root),
            "--output",
            str(output),
        ],
        log_file=log_file,
        timeout=60,
    )
    if output.is_file():
        payload = read_json(output)
    else:
        payload = {"ok": False, "reason": f"SDK probe did not write {output}"}
    payload["exit_code"] = completed.returncode
    return payload


def copy_typecheck_sources(plugin_dir: Path, probe_root: Path) -> None:
    source = plugin_dir / "src"
    destination = probe_root / "src"

    def ignore(directory: str, names: list[str]) -> set[str]:
        if Path(directory).name == "src" and "typings" in names:
            return {"typings"}
        return set()

    shutil.copytree(source, destination, ignore=ignore)
    write_json(
        probe_root / "tsconfig.json",
        {
            "compilerOptions": {
                "strict": True,
                "noImplicitAny": True,
                "noEmit": True,
                "target": "ES2022",
                "module": "NodeNext",
                "moduleResolution": "NodeNext",
                "skipLibCheck": True,
            },
            "include": ["src/**/*.ts"],
        },
    )


def run_typecheck(
    *,
    plugin_dir: Path,
    install_dir: Path,
    typescript: Path,
    output_dir: Path,
) -> dict[str, Any]:
    probe_root = install_dir / "plugin-typecheck"
    copy_typecheck_sources(plugin_dir, probe_root)
    log_file = output_dir / "real-sdk-typecheck.log"
    completed = run_logged(
        [str(typescript), "--project", str(probe_root / "tsconfig.json")],
        cwd=probe_root,
        log_file=log_file,
        timeout=180,
    )
    return {"ok": completed.returncode == 0, "exit_code": completed.returncode, "log": str(log_file)}


def stage_runtime_plugin(plugin_dir: Path, install_dir: Path) -> Path:
    """Copy built plugin artifacts beside the selected OpenClaw installation."""
    destination = install_dir / "extensions/openclaw-channel-bcn"
    shutil.copytree(
        plugin_dir / "dist",
        destination / "dist",
        ignore=shutil.ignore_patterns("node_modules"),
    )
    for filename in ("package.json", "openclaw.plugin.json"):
        source = plugin_dir / filename
        if not source.is_file():
            raise RuntimeError(f"runtime plugin artifact is missing: {source}")
        shutil.copy2(source, destination / filename)
    if (destination / "node_modules").exists():
        raise RuntimeError("isolated runtime plugin unexpectedly contains node_modules")
    return destination


def resolve_runtime_sdk(runtime_plugin_dir: Path, install_dir: Path, output_dir: Path) -> str:
    """Verify Node resolves SDK imports to the selected host package."""
    script = (
        "import { createRequire } from 'node:module';"
        "const require = createRequire(process.argv[1]);"
        "process.stdout.write(require.resolve('openclaw/plugin-sdk/core'));"
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script, str(runtime_plugin_dir / "package.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    log_file = output_dir / "runtime-sdk-resolution.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}\n",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"runtime SDK resolution failed; see {log_file}")
    resolved = Path(completed.stdout.strip()).resolve()
    expected_root = (install_dir / "node_modules/openclaw").resolve()
    try:
        resolved.relative_to(expected_root)
    except ValueError as error:
        raise RuntimeError(
            f"runtime SDK resolved outside selected OpenClaw package: {resolved}"
        ) from error
    return str(resolved)


def open_process(
    command: list[str],
    *,
    log_file: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[Any], IO[str]]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = log_file.open("w", encoding="utf-8")
    handle.write(f"command: {' '.join(command)}\n")
    handle.flush()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return process, handle


def runtime_config(
    *,
    version: str,
    plugin_dir: Path,
    workspace_dir: Path,
    llm_base_url: str,
    bcs_ws_url: str,
    gateway_port: int,
) -> dict[str, Any]:
    model_ref = "compat-openai/compat-model"
    return {
        "meta": {"lastTouchedVersion": version},
        "models": {
            "mode": "merge",
            "providers": {
                "compat-openai": {
                    "baseUrl": f"{llm_base_url}/v1",
                    "apiKey": "openclaw-compat-key",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "compat-model",
                            "name": "AverNet compatibility model",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            "contextWindow": 131072,
                            "maxTokens": 1024,
                        }
                    ],
                }
            },
        },
        "agents": {
            "defaults": {
                "workspace": str(workspace_dir),
                "model": {"primary": model_ref},
                "models": {model_ref: {"alias": "compat-model"}},
            },
            "list": [{"id": "main"}],
        },
        "channels": {
            "bcs": {
                "enabled": True,
                "bcsUrl": bcs_ws_url,
                "botId": "openclaw-compat-bot",
                "botName": "OpenClaw Compatibility Bot",
                "capabilities": {
                    "summary": "OpenClaw compatibility probe",
                    "domains": ["compatibility"],
                    "skills": ["chat"],
                    "scopes": ["local"],
                },
                "heartbeatIntervalMs": 5000,
                "reconnectIntervalMs": 1000,
                "connectionTimeoutMs": 10000,
            }
        },
        "gateway": {
            "port": gateway_port,
            "mode": "local",
            "bind": "loopback",
            "auth": {"mode": "token", "token": "openclaw-compat-gateway-token"},
        },
        "plugins": {
            "load": {"paths": [str(plugin_dir)]},
            "entries": {"openclaw-channel-bcn": {"enabled": True}},
        },
    }


def count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def run_runtime_probe(
    *,
    version: str,
    executable: Path,
    install_dir: Path,
    source_plugin_dir: Path,
    runtime_plugin_dir: Path,
    work_dir: Path,
    output_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    runtime_sdk_entry = resolve_runtime_sdk(runtime_plugin_dir, install_dir, output_dir)
    llm_ready = output_dir / "mock-llm-ready.json"
    llm_requests = output_dir / "mock-llm-requests.jsonl"
    bcs_ready = output_dir / "mock-bcs-ready.json"
    bcs_result = output_dir / "mock-bcs-result.json"
    bcs_frames = output_dir / "mock-bcs-frames.jsonl"
    profile_dir = work_dir / "profile"
    workspace_dir = work_dir / "workspace"
    for directory in (profile_dir, workspace_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "AGENTS.md").write_text(
        "You are a deterministic compatibility-test bot. Follow the user's exact reply instruction.\n",
        encoding="utf-8",
    )

    llm_process: subprocess.Popen[Any] | None = None
    bcs_process: subprocess.Popen[Any] | None = None
    gateway_process: subprocess.Popen[Any] | None = None
    handles: list[IO[str]] = []
    try:
        llm_process, handle = open_process(
            [
                sys.executable,
                str(repository_root() / "scripts/compat/openclaw/mock_llm.py"),
                "--ready-file",
                str(llm_ready),
                "--requests-file",
                str(llm_requests),
                "--response-text",
                EXPECTED_REPLY,
            ],
            log_file=output_dir / "mock-llm.log",
        )
        handles.append(handle)
        llm_info = wait_for_json(llm_ready, timeout=10, process=llm_process)

        bcs_process, handle = open_process(
            [
                "node",
                str(source_plugin_dir / "test/compat/mock_bcs.mjs"),
                "--ready-file",
                str(bcs_ready),
                "--result-file",
                str(bcs_result),
                "--frames-file",
                str(bcs_frames),
                "--expected-text",
                EXPECTED_REPLY,
                "--timeout-ms",
                str(timeout_seconds * 1000),
            ],
            cwd=source_plugin_dir,
            log_file=output_dir / "mock-bcs.log",
        )
        handles.append(handle)
        bcs_info = wait_for_json(bcs_ready, timeout=10, process=bcs_process)

        gateway_port = free_port()
        config = runtime_config(
            version=version,
            plugin_dir=runtime_plugin_dir,
            workspace_dir=workspace_dir,
            llm_base_url=str(llm_info["base_url"]),
            bcs_ws_url=str(bcs_info["ws_url"]),
            gateway_port=gateway_port,
        )
        config_file = profile_dir / "openclaw.json"
        write_json(config_file, config)
        config_file.chmod(0o600)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{install_dir / 'node_modules/.bin'}:{env.get('PATH', '')}",
                "OPENCLAW_DATA_DIR": str(profile_dir),
                "OPENCLAW_STATE_DIR": str(profile_dir),
                "OPENCLAW_CONFIG_PATH": str(config_file),
                "OPENCLAW_WORKSPACE_DIR": str(workspace_dir),
                "OPENCLAW_GATEWAY_TOKEN": "",
                "BOT_DATA_DIR": str(profile_dir),
                "BCS_IGNORE_CREDENTIALS": "1",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            }
        )
        gateway_process, handle = open_process(
            [str(executable), "--profile", "compat", "gateway", "run", "--port", str(gateway_port)],
            cwd=repository_root(),
            env=env,
            log_file=output_dir / "openclaw-gateway.log",
        )
        handles.append(handle)

        bcs_payload = wait_for_json(bcs_result, timeout=timeout_seconds + 10, process=gateway_process)
        llm_request_count = count_jsonl(llm_requests)
        return {
            "ok": bool(bcs_payload.get("ok")) and llm_request_count > 0,
            "bcs": bcs_payload,
            "llm_request_count": llm_request_count,
            "isolated_plugin": True,
            "runtime_sdk_entry": runtime_sdk_entry,
            "gateway_exit_code": gateway_process.poll(),
            "logs": {
                "gateway": str(output_dir / "openclaw-gateway.log"),
                "mock_bcs": str(output_dir / "mock-bcs.log"),
                "mock_llm": str(output_dir / "mock-llm.log"),
            },
        }
    finally:
        terminate_process(gateway_process)
        terminate_process(bcs_process)
        terminate_process(llm_process)
        for handle in handles:
            handle.close()


def status_from_phases(phases: dict[str, dict[str, Any]]) -> str:
    priority = [
        ("install", "FAIL_PACKAGE_INSTALL"),
        ("sdk_imports", "FAIL_SDK_ABI"),
        ("runtime", "FAIL_RUNTIME"),
    ]
    for phase, status in priority:
        if phase in phases and not phases[phase].get("ok"):
            if phase == "runtime" and phases[phase].get("llm_request_count") == 0:
                return "FAIL_LLM_PIPELINE"
            return status
    if "typecheck" in phases and not phases["typecheck"].get("ok"):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def apply_skipped_phase_status(status: str, skipped_phases: list[str]) -> str:
    if status not in {"PASS", "PASS_WITH_WARNINGS"}:
        return status
    if "runtime" in skipped_phases:
        return "INCOMPLETE_SKIPPED_RUNTIME"
    return status


def parse_args() -> argparse.Namespace:
    repo_root = repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--plugin-dir", type=Path, default=default_plugin_dir(repo_root))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--npm-cache", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--skip-typecheck", action="store_true")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument(
        "--skip-plugin-build",
        action="store_true",
        help="reuse an already-built plugin (used by the matrix runner)",
    )
    parser.add_argument("--keep-workdir", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 1,
        "openclaw_version": args.version,
        "status": "INFRA_ERROR",
        "started_at": utc_now(),
        "phases": {},
    }
    work_dir = Path(tempfile.mkdtemp(prefix=f"avernet-openclaw-{args.version}-"))

    try:
        plugin_build = ensure_plugin_build(
            args.plugin_dir.resolve(),
            args.output_dir,
            skip_build=args.skip_plugin_build,
        )
        install_dir = work_dir / "host"
        try:
            executable, package = install_openclaw(
                args.version,
                install_dir,
                args.output_dir,
                args.npm_cache,
            )
            result["phases"]["install"] = {
                "ok": True,
                "resolved_version": package.get("version"),
                "engines": package.get("engines", {}),
            }
            result["node_version"] = subprocess.run(
                ["node", "--version"], capture_output=True, text=True, check=False
            ).stdout.strip()
            result["npm_version"] = subprocess.run(
                ["npm", "--version"], capture_output=True, text=True, check=False
            ).stdout.strip()
        except Exception as error:
            result["phases"]["install"] = {"ok": False, "error": str(error)}
            raise

        sdk_probe = run_sdk_probe(
            plugin_dir=args.plugin_dir.resolve(),
            package_root=install_dir / "node_modules/openclaw",
            output_dir=args.output_dir,
        )
        result["phases"]["sdk_imports"] = sdk_probe

        if not args.skip_typecheck:
            result["phases"]["typecheck"] = run_typecheck(
                plugin_dir=args.plugin_dir.resolve(),
                install_dir=install_dir,
                typescript=Path(plugin_build["typescript"]),
                output_dir=args.output_dir,
            )

        if not args.skip_runtime:
            try:
                runtime_plugin_dir = stage_runtime_plugin(args.plugin_dir.resolve(), install_dir)
                result["phases"]["runtime"] = run_runtime_probe(
                    version=args.version,
                    executable=executable,
                    install_dir=install_dir,
                    source_plugin_dir=args.plugin_dir.resolve(),
                    runtime_plugin_dir=runtime_plugin_dir,
                    work_dir=work_dir / "runtime",
                    output_dir=args.output_dir,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as error:
                result["phases"]["runtime"] = {"ok": False, "error": str(error)}

        result["status"] = status_from_phases(result["phases"])
        skipped_phases = [
            phase
            for phase, skipped in (("typecheck", args.skip_typecheck), ("runtime", args.skip_runtime))
            if skipped
        ]
        if skipped_phases:
            result["skipped_phases"] = skipped_phases
        result["status"] = apply_skipped_phase_status(result["status"], skipped_phases)
    except Exception as error:
        result["error"] = str(error)
        result["status"] = status_from_phases(result["phases"])
        if result["status"] == "PASS":
            result["status"] = "INFRA_ERROR"
    finally:
        result["finished_at"] = utc_now()
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        if args.keep_workdir:
            result["work_dir"] = str(work_dir)
        else:
            shutil.rmtree(work_dir, ignore_errors=True)
        write_json(args.output_dir / "result.json", result)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "PASS_WITH_WARNINGS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
