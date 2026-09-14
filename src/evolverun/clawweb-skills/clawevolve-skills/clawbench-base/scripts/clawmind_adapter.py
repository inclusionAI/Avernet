#!/usr/bin/env python3
"""ClawMind adapter for running ClawBench from ClawWeb templates.

This script is intentionally self-contained because ClawMind executes workflow
nodes as external commands. The workflow should stay declarative and delegate
ClawBench-specific behavior here.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


# Debug overrides. Keep empty for normal workflow execution.
# Priority: script override > workflow env > required validation.
OVERRIDE_AGENTBENCH_HOME = ""
OVERRIDE_CLAWWEB_URL = ""
OVERRIDE_REPORT_TEMPLATE_PATH = ""

# Global owner user id cached by get_owner_user_id()
_owner_user_id: str | None = None
API_TIMEOUT_SECONDS = int(os.environ.get("CLAWBENCH_API_TIMEOUT_SECONDS", "180"))
API_RETRY_ATTEMPTS = int(os.environ.get("CLAWBENCH_API_RETRY_ATTEMPTS", "3"))
API_RETRY_BASE_DELAY_SECONDS = float(os.environ.get("CLAWBENCH_API_RETRY_BASE_DELAY_SECONDS", "1"))


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    if not value or (value.startswith("{{") and value.endswith("}}")):
        return default
    return value


def set_child_env_if_empty_or_template(child_env: dict[str, str], name: str, value: str) -> None:
    current = child_env.get(name, "")
    if value and (not current or (current.startswith("{{") and current.endswith("}}"))):
        child_env[name] = value


def required_env(name: str) -> str:
    value = env(name, "")
    if not value:
        fail(f"{name} is required")
    return value


def config_value(name: str, override: str = "", *, required: bool = True) -> str:
    if override:
        return override
    if required:
        return required_env(name)
    return env(name, "")


def json_out(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(exit_code)


def fail(message: str, **extra: Any) -> None:
    payload = {"status": "failed", "error": message}
    payload.update(extra)
    print(message, file=sys.stderr)
    json_out(payload, 1)


def _read_credentials_file(path: Path) -> dict[str, str]:
    """Parse simple key=value credentials file."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                result[key.strip()] = val.strip()
    except Exception:
        pass
    return result



def parse_json_env(name: str) -> Any:
    raw = env(name, "")
    if not raw:
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(raw)
            if isinstance(parsed, str):
                try:
                    return json.loads(parsed)
                except Exception:
                    return parsed
            return parsed
        except Exception:
            continue
    return None

def get_owner_user_id() -> str:
    """Resolve OWNER_ID from env or ~/.credentials.

    Priority:
    1. CLAWBENCH_OWNER_ID env var
    2. OWNER_ID env var
    3. ~/.credentials OWNER_ID
    4. ~/.credentials owner_id
    """
    global _owner_user_id
    if _owner_user_id is not None:
        return _owner_user_id

    owner_id = env("CLAWBENCH_OWNER_ID", "")
    if not owner_id:
        owner_id = env("OWNER_ID", "")
    if not owner_id:
        creds = _read_credentials_file(Path.home() / ".credentials")
        owner_id = creds.get("OWNER_ID", "")
    if not owner_id:
        owner_id = creds.get("owner_id", "")

    if not owner_id:
        fail(
            "Missing OWNER_ID. Set CLAWBENCH_OWNER_ID/OWNER_ID or add OWNER_ID=xxx to ~/.credentials."
        )

    _owner_user_id = owner_id
    return owner_id


def bench_domain_api_base(clawweb_url: str, domain_id: str) -> str:
    owner_id = urllib.parse.quote(get_owner_user_id(), safe="")
    encoded_domain_id = urllib.parse.quote(domain_id, safe="")
    return f"{clawweb_url}/api/bench/domains/{owner_id}/{encoded_domain_id}"


def api_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    owner_id = get_owner_user_id()
    if owner_id:
        headers["X-User-Id"] = owner_id
    max_attempts = max(1, API_RETRY_ATTEMPTS)
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8")
                if attempt > 1:
                    log_stderr(f"API retry succeeded attempt={attempt}/{max_attempts} method={method} url={url}")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            if attempt >= max_attempts:
                raise RuntimeError(f"{method} {url} failed: {err.code} {err.reason} {body}") from err
            last_error = err
            delay = _retry_delay(attempt)
            log_stderr(
                f"API retryable HTTP error attempt={attempt}/{max_attempts} "
                f"nextDelay={delay:.1f}s method={method} url={url} status={err.code}"
            )
            time.sleep(delay)
        except Exception as err:
            if attempt >= max_attempts:
                raise RuntimeError(f"{method} {url} error: {err}") from err
            last_error = err
            delay = _retry_delay(attempt)
            log_stderr(
                f"API retryable error attempt={attempt}/{max_attempts} "
                f"nextDelay={delay:.1f}s method={method} url={url} error={err}"
            )
            time.sleep(delay)
    raise RuntimeError(f"{method} {url} error: {last_error}")


def api_ok(method: str, url: str, payload: dict[str, Any]) -> bool:
    try:
        api_json(method, url, payload)
        return True
    except RuntimeError as err:
        log_stderr(str(err))
        return False


def selected_version(template: dict[str, Any], requested: str) -> int:
    if requested:
        return int(requested)
    published = template.get("publishedVersion")
    latest = template.get("latestVersion")
    return int(published or latest or 1)


def template_has_published_version(template: dict[str, Any]) -> bool:
    status = str(template.get("status", "")).lower()
    published = template.get("publishedVersion")
    return status == "published" or published not in (None, "", 0, "0")


def generate_run_stamp() -> str:
    """Generate a unique run stamp: YYYYMMDD_HHMMSS_<random6>"""
    now = datetime.now()
    rand = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{rand}"


def safe_path_segment(value: str, fallback: str = "run") -> str:
    value = (value or "").strip()
    if not value:
        return fallback
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def workspace_clawbench_result_root(run_id: str) -> Path:
    return Path.home() / ".openclaw" / "workspace" / "clawbench_results" / safe_path_segment(run_id)


def resolve_benchmark_input(agentbench_home: Path, benchmark_dir: str) -> tuple[Path, str]:
    """Return absolute input dir and benchmark arg accepted by scripts/benchmark.py."""
    benchmark_path = Path(benchmark_dir)
    tasks_root = agentbench_home / "tasks"
    if benchmark_path.is_absolute():
        input_dir = benchmark_path
    else:
        input_dir = tasks_root / benchmark_dir

    try:
        benchmark_arg = str(input_dir.resolve().relative_to(tasks_root.resolve()))
    except ValueError:
        benchmark_arg = benchmark_dir
    return input_dir, benchmark_arg


def _remove_existing_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def relocate_benchmark_input(
    agentbench_home: Path,
    source_dir: Path,
    bench_run_id: str,
    target_input_dir: Path | None = None,
) -> tuple[Path, str]:
    """Expose generated task input under AgentBench tasks for one run.

    Evolve callers keep their explicit task-owned input directory. Standalone
    callers retain the legacy workspace/clawbench_results copy behavior.

    AgentBench currently resolves --benchmark relative to {skill}/tasks, so we
    create a lightweight compatibility link under tasks/ that points at the
    workspace-owned input directory.
    """
    input_dir = target_input_dir or (workspace_clawbench_result_root(bench_run_id) / "input")
    input_dir.parent.mkdir(parents=True, exist_ok=True)

    if source_dir.resolve() != input_dir.resolve():
        if not source_dir.is_dir():
            raise FileNotFoundError(f"benchmark source directory not found: {source_dir}")
        _remove_existing_path(input_dir)
        shutil.copytree(source_dir, input_dir)
    elif not input_dir.is_dir():
        raise FileNotFoundError(f"relocated benchmark input not found: {input_dir}")

    link_root = "clawbench_runtime" if target_input_dir is not None else "clawbench_results"
    tasks_link = agentbench_home / "tasks" / link_root / safe_path_segment(bench_run_id) / "input"
    _remove_existing_path(tasks_link)
    tasks_link.parent.mkdir(parents=True, exist_ok=True)
    try:
        tasks_link.symlink_to(input_dir, target_is_directory=True)
    except OSError:
        if target_input_dir is not None:
            raise
        shutil.copytree(input_dir, tasks_link)

    benchmark_arg = str(tasks_link.relative_to(agentbench_home / "tasks"))
    return input_dir, benchmark_arg


def read_text_tail(path: Path, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def extract_task_id_from_md(content_md: str) -> str | None:
    """Extract task id from markdown frontmatter.

    Priority: id > taskId > task_id
    """
    match = re.search(r"^---\s*\n(.*?)\n---", content_md, re.DOTALL)
    if not match:
        return None
    frontmatter_text = match.group(1)
    for key in ("id", "taskId", "task_id"):
        m = re.search(rf"^{key}\s*:\s*(.+)$", frontmatter_text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def action_load_template() -> None:
    domain_id = env("DOMAIN_ID")
    template_name = env("TEMPLATE_NAME")
    template_version = env("TEMPLATE_VERSION")
    agentbench_home = Path(config_value("AGENTBENCH_HOME", OVERRIDE_AGENTBENCH_HOME))
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")

    if not domain_id:
        fail("DOMAIN_ID is required")

    domain_api_base = bench_domain_api_base(clawweb_url, domain_id)

    run_stamp = generate_run_stamp()
    runtime_dir = agentbench_home / "tasks" / "clawweb_runtime" / domain_id / "runs" / run_stamp
    runtime_dir.mkdir(parents=True, exist_ok=True)
    benchmark_dir = f"clawweb_runtime/{domain_id}/runs/{run_stamp}"

    # Domain run: no TEMPLATE_NAME means load all published templates
    if not template_name:
        templates_resp = api_json(
            "GET",
            f"{domain_api_base}/templates?status=published",
        )
        templates = templates_resp if isinstance(templates_resp, list) else templates_resp.get("data", [])
        if not templates:
            all_templates_resp = api_json("GET", f"{domain_api_base}/templates")
            all_templates = all_templates_resp if isinstance(all_templates_resp, list) else all_templates_resp.get("data", [])
            templates = [t for t in all_templates if template_has_published_version(t)]
        if not templates:
            fail("No published templates found for domain", domainId=domain_id, ownerUserId=get_owner_user_id())

        # First pass: fetch all template contents and compute taskIds
        pending: list[dict[str, Any]] = []
        for template in templates:
            t_name = str(template.get("templateName", ""))
            if not t_name:
                continue
            target_version = selected_version(template, "")

            # Fetch full template to get contentMd for the target version
            encoded_name = urllib.parse.quote(t_name, safe="")
            full_template = api_json(
                "GET",
                f"{domain_api_base}/templates/{encoded_name}",
            )
            content_md = ""
            for version in full_template.get("versions", []):
                if int(version.get("version", 0)) == target_version:
                    content_md = version.get("contentMd", "")
                    break
            if not content_md:
                print(f"[clawmind_adapter] Warning: content not found for {t_name} v{target_version}", file=sys.stderr)
                continue

            task_id = extract_task_id_from_md(content_md) or t_name
            pending.append({
                "taskId": task_id,
                "templateName": t_name,
                "templateVersion": target_version,
                "contentMd": content_md,
            })

        # Detect duplicate taskIds before writing any files
        task_id_to_names: dict[str, list[str]] = {}
        for item in pending:
            tid = item["taskId"]
            task_id_to_names.setdefault(tid, []).append(item["templateName"])
        duplicates = {tid: names for tid, names in task_id_to_names.items() if len(names) > 1}
        if duplicates:
            msgs = []
            for tid, names in duplicates.items():
                msgs.append(f"Duplicate taskId in domain {domain_id}: {tid}; templates: {', '.join(names)}")
            fail("; ".join(msgs))

        # Second pass: write files now that duplicates are ruled out
        loaded: list[dict[str, Any]] = []
        task_files: list[dict[str, Any]] = []
        for item in pending:
            task_id = item["taskId"]
            t_name = item["templateName"]
            target_version = item["templateVersion"]
            content_md = item["contentMd"]
            filename = f"{task_id}.md" if task_id.startswith("task_") else f"task_{task_id}.md"
            task_file = runtime_dir / filename
            task_file.write_text(content_md, encoding="utf-8")
            loaded.append({"taskId": task_id, "templateName": t_name, "templateVersion": target_version})
            task_files.append({
                "taskId": task_id,
                "templateName": t_name,
                "templateVersion": target_version,
                "path": str(task_file),
            })

        if not loaded:
            fail("No template content could be loaded", domainId=domain_id)

        json_out(
            {
                "status": "ok",
                "runScope": "domain",
                "runStamp": run_stamp,
                "domainId": domain_id,
                "templateCount": len(loaded),
                "templates": loaded,
                "taskFiles": task_files,
                "runtimeDir": str(runtime_dir),
                "benchmarkDir": benchmark_dir,
            }
        )

    # Single template run
    encoded_name = urllib.parse.quote(template_name, safe="")
    template = api_json(
        "GET",
        f"{domain_api_base}/templates/{encoded_name}",
    )
    target_version = selected_version(template, template_version)

    content_md = ""
    for version in template.get("versions", []):
        if int(version.get("version", 0)) == target_version:
            content_md = version.get("contentMd", "")
            break
    if not content_md:
        fail("Version content not found", targetVersion=target_version)

    task_id = extract_task_id_from_md(content_md) or template_name
    filename = f"{task_id}.md" if task_id.startswith("task_") else f"task_{task_id}.md"
    task_file = runtime_dir / filename
    task_file.write_text(content_md, encoding="utf-8")

    template_entry = {"taskId": task_id, "templateName": template_name, "templateVersion": target_version}
    json_out(
        {
            "status": "ok",
            "runScope": "template",
            "runStamp": run_stamp,
            "domainId": domain_id,
            "templateName": template_name,
            "templateVersion": target_version,
            "templateCount": 1,
            "templates": [template_entry],
            "taskFiles": [{**template_entry, "path": str(task_file)}],
            "runtimeDir": str(runtime_dir),
            "benchmarkDir": benchmark_dir,
            "taskFile": str(task_file),
        }
    )



def validate_domain_run_templates(templates: Any, template_count: int | None) -> list[dict[str, Any]]:
    if not isinstance(templates, list) or not templates:
        fail("Domain run requires non-empty TEMPLATES_JSON")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(templates):
        if not isinstance(item, dict):
            fail("Invalid domain run template entry", index=index, reason="entry is not an object")
        task_id = str(item.get("taskId") or "").strip()
        template_name = str(item.get("templateName") or "").strip()
        raw_version = item.get("templateVersion")
        if not task_id:
            fail("Invalid domain run template entry", index=index, reason="missing taskId")
        if not template_name:
            fail("Invalid domain run template entry", index=index, reason="missing templateName")
        try:
            template_version = int(raw_version)
        except Exception:
            fail("Invalid domain run template entry", index=index, reason="invalid templateVersion")
        if template_version <= 0:
            fail("Invalid domain run template entry", index=index, reason="invalid templateVersion")
        normalized.append({
            "taskId": task_id,
            "templateName": template_name,
            "templateVersion": template_version,
        })

    if template_count is not None and len(normalized) != template_count:
        fail(
            "Domain run template count mismatch",
            templateCount=template_count,
            templatesLength=len(normalized),
        )
    return normalized

def action_create_run() -> None:
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")
    flow_id = env("FLOW_ID") or env("WORKFLOW_ENGINE_FLOW_ID")
    domain_id = env("DOMAIN_ID")
    template_name = env("TEMPLATE_NAME")
    template_version = env("TEMPLATE_VERSION", "1")
    run_scope = env("RUN_SCOPE", "template")
    template_count_str = env("TEMPLATE_COUNT", "")

    if not domain_id:
        fail("DOMAIN_ID is required")

    payload: dict[str, Any] = {
        "domainId": domain_id,
        "status": "running",
        "model": env("MODEL", "antchat/GLM-5.1"),
        "suite": env("SUITE", "all"),
        "scene": env("SCENE", "openclaw-clawbench"),
    }
    if flow_id:
        payload["clawmindFlowId"] = flow_id

    run_config: dict[str, Any] = {"runScope": run_scope}
    template_count: int | None = None
    if template_count_str:
        try:
            template_count = int(template_count_str)
        except Exception:
            fail("Invalid TEMPLATE_COUNT", templateCount=template_count_str)
        run_config["templateCount"] = template_count
    templates = parse_json_env("TEMPLATES_JSON")

    if run_scope == "domain":
        normalized_templates = validate_domain_run_templates(templates, template_count)
        run_config["templates"] = normalized_templates
        payload["templateName"] = "__domain__"
        payload["templateVersion"] = 0
        payload["runConfig"] = run_config
    else:
        if not template_name:
            fail("TEMPLATE_NAME is required for template run")
        payload["templateName"] = template_name
        payload["templateVersion"] = int(template_version)
        payload["runConfig"] = run_config

    resp = api_json("POST", f"{clawweb_url}/api/bench/runs", payload)
    json_out(
        {
            "status": "ok",
            "benchRunId": resp.get("benchRunId", ""),
            "detailUrl": resp.get("detailUrl", ""),
        }
    )


def action_run_agentbench() -> None:
    agentbench_home = Path(config_value("AGENTBENCH_HOME", OVERRIDE_AGENTBENCH_HOME))
    benchmark_dir = env("BENCHMARK_DIR")
    bench_run_id = env("BENCH_RUN_ID")
    suite = env("SUITE", "all")
    model = env("MODEL", "antchat/GLM-5.1")
    scene = env("SCENE", "openclaw-clawbench")
    judge = env("JUDGE", "")
    judge_base_url = env("JUDGE_BASE_URL", "")
    judge_api_key = env("JUDGE_API_KEY", "")
    output_dir = env("OUTPUT_DIR", "")
    input_dir = env("INPUT_DIR", "")
    openclaw_execution_mode = env("OPENCLAW_EXECUTION_MODE", env("CLAWBENCH_OPENCLAW_EXECUTION_MODE", "local"))
    run_stamp = env("RUN_STAMP", "")
    domain_id = env("DOMAIN_ID", "")

    started_at = int(time.time())
    if not agentbench_home.is_dir():
        json_out({"status": "failed", "error": "AGENTBENCH_HOME not found", "path": str(agentbench_home)})
    if not benchmark_dir:
        json_out({"status": "failed", "error": "BENCHMARK_DIR is required"})

    benchmark_input_dir, benchmark_arg = resolve_benchmark_input(agentbench_home, benchmark_dir)
    # Backward-compatible retry recovery: older adapters moved work_dir/input
    # into the run-owned result directory before benchmark execution.  A failed
    # run then reused load_templates state whose benchmarkDir no longer existed.
    # Prefer the original source, but fall back to the durable relocated input.
    if not benchmark_input_dir.is_dir() and bench_run_id and not input_dir:
        relocated_input_dir = workspace_clawbench_result_root(bench_run_id) / "input"
        if relocated_input_dir.is_dir():
            benchmark_input_dir = relocated_input_dir

    if not benchmark_input_dir.is_dir():
        json_out({
            "status": "failed",
            "error": "Benchmark directory not found",
            "benchmarkDir": benchmark_dir,
            "benchmarkInputDir": str(benchmark_input_dir),
        })

    if bench_run_id:
        benchmark_input_dir, benchmark_arg = relocate_benchmark_input(
            agentbench_home,
            benchmark_input_dir,
            bench_run_id,
            Path(input_dir).expanduser().resolve() if input_dir else None,
        )

    # Resolve output directory
    if output_dir:
        resolved_output_dir = output_dir
    elif bench_run_id:
        resolved_output_dir = str(workspace_clawbench_result_root(bench_run_id) / "output")
    else:
        if not run_stamp or not domain_id:
            json_out({"status": "failed", "error": "OUTPUT_DIR empty but RUN_STAMP and DOMAIN_ID required for auto output dir"})
        resolved_output_dir = str(Path.home() / ".openclaw" / "workspace" / "clawbench_results" / safe_path_segment(run_stamp) / safe_path_segment(domain_id) / "output")

    Path(resolved_output_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(resolved_output_dir) / "run_agentbench.log"
    adapter_log(
        "run-agentbench started "
        f"benchRunId={bench_run_id or '-'} "
        f"domainId={domain_id or '-'} "
        f"benchmarkDir={benchmark_dir or '-'} "
        f"outputDir={resolved_output_dir} "
        f"model={model} suite={suite} scene={scene}",
        resolved_output_dir,
    )

    cmd = [
        sys.executable,
        "scripts/benchmark.py",
        "--model",
        model,
        "--benchmark",
        benchmark_arg,
        "--suite",
        suite,
        "--scene",
        scene,
        "--output-dir",
        resolved_output_dir,
        "--no-fail-fast",
    ]
    if judge:
        cmd.extend(["--judge", judge])
    if judge_base_url:
        cmd.extend(["--judge-base-url", judge_base_url])
    if judge_api_key:
        cmd.extend(["--judge-api-key", judge_api_key])

    child_env = os.environ.copy()
    child_env["CLAWBENCH_OPENCLAW_EXECUTION_MODE"] = openclaw_execution_mode
    owner_id = get_owner_user_id()
    if owner_id:
        set_child_env_if_empty_or_template(child_env, "CLAWBENCH_OWNER_ID", owner_id)
    if bench_run_id:
        set_child_env_if_empty_or_template(child_env, "CLAWBENCH_BENCH_RUN_ID", bench_run_id)
        set_child_env_if_empty_or_template(child_env, "CLAWBENCH_CALLBACK_ENABLED", "1")

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"$ {' '.join(cmd)}\n")
            log_file.write(f"cwd={agentbench_home}\n")
            log_file.write(f"benchmarkInputDir={benchmark_input_dir}\n")
            log_file.write(f"outputDir={resolved_output_dir}\n")
            log_file.write(f"openclawExecutionMode={openclaw_execution_mode}\n")
            log_file.write(
                "callback "
                f"enabled={child_env.get('CLAWBENCH_CALLBACK_ENABLED', '')} "
                f"benchRunId={child_env.get('CLAWBENCH_BENCH_RUN_ID', '')} "
                f"ownerId={'set' if child_env.get('CLAWBENCH_OWNER_ID') else 'missing'} "
                f"clawwebUrl={child_env.get('CLAWWEB_URL', '')}\n\n"
            )
            log_file.flush()
            proc = subprocess.run(
                cmd,
                cwd=str(agentbench_home),
                check=False,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=child_env,
            )
    finally:
        if input_dir and bench_run_id:
            runtime_link_root = agentbench_home / "tasks" / "clawbench_runtime" / safe_path_segment(bench_run_id)
            _remove_existing_path(runtime_link_root)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"\n[clawmind_adapter] benchmark.py finished exitCode={proc.returncode}\n")
    status = "succeeded" if proc.returncode == 0 else "failed"
    log_tail = "" if proc.returncode == 0 else read_text_tail(log_path)
    error = "" if proc.returncode == 0 else f"ClawBench execution failed (exit code {proc.returncode}). See {log_path}"
    completed_at = int(time.time())

    latest_result = find_report(resolved_output_dir, scene, cwd=agentbench_home)
    adapter_log(
        "run-agentbench finished "
        f"benchRunId={bench_run_id or '-'} exitCode={proc.returncode} "
        f"status={status} resultPath={latest_result or '-'}",
        resolved_output_dir,
    )
    json_out(
        {
            "status": status,
            "benchmarkDir": benchmark_dir,
            "benchmarkArg": benchmark_arg,
            "benchmarkInputDir": str(benchmark_input_dir),
            "suite": suite,
            "model": model,
            "scene": scene,
            "outputDir": resolved_output_dir,
            "resultPath": latest_result,
            "runStamp": run_stamp,
            "benchRunId": bench_run_id,
            "startedAt": started_at,
            "completedAt": completed_at,
            "exitCode": proc.returncode,
            "logPath": str(log_path),
            "logTail": log_tail,
            "error": error,
        }
    )


def find_report(output_dir: str, scene: str, cwd: Path | None = None) -> str:
    base = Path(output_dir)
    if cwd and not base.is_absolute():
        base = cwd / base

    candidates: list[str] = []
    if scene:
        scene_dir = base / scene
        if scene_dir.is_dir():
            candidates = glob.glob(str(scene_dir / "**" / "*benchmark_report.json"), recursive=True)
    if not candidates and base.is_dir():
        candidates = glob.glob(str(base / "**" / "*benchmark_report.json"), recursive=True)
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def normalize_task_status(raw: Any) -> str:
    status = str(raw or "pending").lower()
    if status == "success":
        return "succeeded"
    if status == "timeout":
        return "timeout"
    if status in {"error", "failed"}:
        return "failed"
    return status


def adapter_log(message: str, output_dir: str = "") -> None:
    line = f"[clawmind_adapter] {message}"
    print(line, file=sys.stderr)
    if not output_dir:
        return
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        with (out / "clawmind_adapter.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def find_archived_transcripts(output_dir: str, scene: str, cwd: Path | None = None) -> list[Path]:
    """Find clawbench archived transcript JSONL files under output benchmark dirs."""
    if not output_dir:
        return []
    base = Path(output_dir)
    if cwd and not base.is_absolute():
        base = cwd / base
    if not base.is_dir():
        return []

    candidates: list[Path] = []
    search_roots = [base]
    if scene:
        scene_dir = base / "benchmark" / scene
        if scene_dir.is_dir():
            search_roots.insert(0, scene_dir)
    for root in search_roots:
        for pattern in ("**/*_transcripts/*.jsonl", "**/*_transcripts/*.ndjson"):
            candidates.extend(Path(p) for p in glob.glob(str(root / pattern), recursive=True))

    seen: set[str] = set()
    unique: list[Path] = []
    for path in sorted(candidates):
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def summarize_jsonl_transcript(path: Path) -> dict[str, Any]:
    event_count = 0
    first_event_type = ""
    last_event_type = ""
    session_id = ""
    workflow_id = ""
    node_count = 0
    synthetic_nodes: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                event_count += 1
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                event_type = str(item.get("type") or item.get("event") or "")
                if event_type:
                    if not first_event_type:
                        first_event_type = event_type
                    last_event_type = event_type
                if not session_id and item.get("id") and event_type == "session":
                    session_id = str(item["id"])
                if event_type == "__manifest__":
                    workflow_id = item.get("workflow_trace", {}).get("workflow_id", "") if isinstance(item.get("workflow_trace"), dict) else ""
                    node_count = item.get("node_count", 0)
                    synthetic_nodes = item.get("synthetic_nodes", [])
    except Exception:
        pass
    result = {
        "eventCount": event_count,
        "firstEventType": first_event_type,
        "lastEventType": last_event_type,
        "sessionId": session_id,
    }
    if workflow_id:
        result["workflowId"] = workflow_id
    if node_count:
        result["workflowNodeCount"] = node_count
    if synthetic_nodes:
        result["workflowSyntheticNodes"] = synthetic_nodes
    return result


def fetch_existing_session_artifact_keys(clawweb_url: str, bench_run_id: str, output_dir: str) -> set[tuple[str, str]]:
    try:
        artifacts = api_json(
            "GET",
            f"{clawweb_url}/api/bench/runs/{bench_run_id}/artifacts?artifactType=session",
        )
    except Exception as exc:
        adapter_log(f"Warning: failed to list existing session artifacts: {exc}", output_dir)
        return set()

    rows = artifacts if isinstance(artifacts, list) else artifacts.get("data", [])
    keys: set[tuple[str, str]] = set()
    if not isinstance(rows, list):
        return keys
    for row in rows:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("taskId") or "")
        filename = str(row.get("filename") or "")
        if task_id and filename:
            keys.add((task_id, filename))
    return keys


def _api_ok_with_retry(
    *,
    clawweb_url: str,
    bench_run_id: str,
    payload: dict[str, Any],
    max_retries: int = 3,
    output_dir: str = "",
) -> bool:
    """Call api_ok with exponential backoff retry on network and HTTP errors."""
    last_error: str = ""
    for attempt in range(max_retries):
        try:
            ok = api_ok(
                "POST",
                f"{clawweb_url}/api/bench/runs/{bench_run_id}/artifacts",
                payload,
            )
            if ok:
                return True
            # api_ok returned False (non-200 HTTP status); treat as retryable
            last_error = "HTTP non-200"
        except Exception as exc:
            last_error = str(exc)

        if attempt < max_retries - 1:
            delay = (2 ** attempt) * API_RETRY_BASE_DELAY_SECONDS
            adapter_log(
                f"Upload attempt {attempt + 1}/{max_retries} failed for {payload.get('taskId', '?')}: "
                f"{last_error}, retrying in {delay:.1f}s...",
                output_dir,
            )
            time.sleep(delay)
        else:
            adapter_log(
                f"Upload failed after {max_retries} attempts for {payload.get('taskId', '?')}: {last_error}",
                output_dir,
            )
            return False
    return False


def upload_transcript_artifacts(
    *,
    clawweb_url: str,
    bench_run_id: str,
    output_dir: str,
    scene: str,
    agentbench_home: Path,
    task_usage_map: dict[str, Any],
) -> tuple[int, int]:
    uploaded = 0
    skipped = 0
    max_bytes = 10 * 1024 * 1024
    transcript_paths = find_archived_transcripts(output_dir, scene, cwd=agentbench_home)
    adapter_log(f"Found {len(transcript_paths)} archived transcript artifact(s) under {output_dir}", output_dir)
    existing_keys = fetch_existing_session_artifact_keys(clawweb_url, bench_run_id, output_dir)
    if existing_keys:
        adapter_log(f"Found {len(existing_keys)} existing session artifact(s) in ClawWeb", output_dir)
    for path in transcript_paths:
        try:
            task_id = path.stem
            if (task_id, path.name) in existing_keys:
                skipped += 1
                adapter_log(f"Skipping existing session artifact taskId={task_id} filename={path.name}", output_dir)
                continue
            size_bytes = path.stat().st_size
            if size_bytes > max_bytes:
                skipped += 1
                adapter_log(f"Skipping transcript artifact over 10MB: {path}", output_dir)
                continue
            content_text = path.read_text(encoding="utf-8", errors="replace")
            summary = summarize_jsonl_transcript(path)
            usage = task_usage_map.get(task_id)
            if isinstance(usage, dict):
                summary["usage"] = usage
                if usage.get("total_tokens") is not None:
                    summary["totalTokens"] = usage.get("total_tokens")
            summary.update({
                "source": "clawbench_transcript_archive",
                "sourcePath": str(path),
                "sizeBytes": size_bytes,
            })
            ok = _api_ok_with_retry(
                clawweb_url=clawweb_url,
                bench_run_id=bench_run_id,
                payload={
                    "artifactType": "session",
                    "taskId": task_id,
                    "filename": path.name,
                    "contentType": "application/x-ndjson",
                    "contentText": content_text,
                    "summary": summary,
                },
                output_dir=output_dir,
            )
            if ok:
                uploaded += 1
                adapter_log(f"Uploaded session artifact taskId={task_id} path={path}", output_dir)
            else:
                skipped += 1
                adapter_log(f"Failed to upload session artifact taskId={task_id} path={path}", output_dir)
        except Exception as exc:
            skipped += 1
            adapter_log(f"Failed to upload transcript artifact {path}: {exc}", output_dir)
    adapter_log(f"Session artifact upload finished: uploaded={uploaded}, skipped={skipped}", output_dir)
    return uploaded, skipped


def task_execution_time_ms(task: dict[str, Any]) -> int | float | None:
    """Normalize benchmark report execution time fields to milliseconds."""
    if task.get("execution_time_ms") is not None:
        return task.get("execution_time_ms")
    if task.get("duration_ms") is not None:
        return task.get("duration_ms")
    execution_time_seconds = task.get("execution_time")
    if execution_time_seconds is None:
        return None
    try:
        return int(float(execution_time_seconds) * 1000)
    except (TypeError, ValueError):
        return None


def action_upload_results() -> None:
    bench_run_id = env("BENCH_RUN_ID")
    status = env("STATUS", "failed")
    report_path = env("RESULT_PATH")
    started_at = env("STARTED_AT")
    completed_at = env("COMPLETED_AT")
    error_text = env("ERROR_TEXT")
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")
    output_dir = env("OUTPUT_DIR", "")
    scene = env("SCENE")
    agentbench_home = Path(config_value("AGENTBENCH_HOME", OVERRIDE_AGENTBENCH_HOME))
    templates_json = env("TEMPLATES_JSON", "")
    adapter_log(
        "upload-results started "
        f"benchRunId={bench_run_id or '-'} status={status} "
        f"resultPath={report_path or '-'} outputDir={output_dir or '-'} scene={scene or '-'}",
        output_dir,
    )

    # Build taskId -> {templateName, templateVersion} mapping
    # Also build templateName -> same for fallback
    task_id_map: dict[str, dict[str, Any]] = {}
    template_name_map: dict[str, dict[str, Any]] = {}
    if templates_json:
        try:
            templates_list = json.loads(templates_json)
            if isinstance(templates_list, list):
                for t in templates_list:
                    t_name = str(t.get("templateName", ""))
                    t_id = str(t.get("taskId", ""))
                    mapping = {
                        "templateName": t_name,
                        "templateVersion": t.get("templateVersion"),
                        "taskId": t_id,
                    }
                    if t_id:
                        task_id_map[t_id] = mapping
                    if t_name:
                        template_name_map[t_name] = mapping
        except Exception:
            pass

    # Resolve report path if not directly provided
    if not report_path or not Path(report_path).is_file():
        # If output_dir is empty, we cannot infer; try to use find_report with cwd
        report_path = find_report(output_dir, scene, cwd=agentbench_home)

    if not report_path or not Path(report_path).is_file():
        if bench_run_id:
            api_ok(
                "PUT",
                f"{clawweb_url}/api/bench/runs/{bench_run_id}",
                {
                    "status": "failed",
                    "errorText": error_text or "No benchmark report found",
                    "completedAt": int(time.time()),
                },
            )
        adapter_log(f"upload-results failed: no benchmark report found resultPath={report_path or '-'}", output_dir)
        json_out({"status": "failed", "error": "No benchmark report found"})

    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except Exception as exc:
        if bench_run_id:
            api_ok(
                "PUT",
                f"{clawweb_url}/api/bench/runs/{bench_run_id}",
                {
                    "status": "failed",
                    "errorText": f"Failed to parse benchmark report: {exc}",
                    "completedAt": int(time.time()),
                },
            )
        adapter_log(f"upload-results failed: report parse error reportPath={report_path} error={exc}", output_dir)
        json_out({"status": "failed", "error": f"Failed to parse benchmark report: {exc}"})
    tasks = report.get("tasks", [])
    if not tasks and isinstance(report, dict) and ("task_id" in report or "id" in report):
        tasks = [report]
    adapter_log(f"upload-results loaded report taskCount={len(tasks)} reportPath={report_path}", output_dir)

    results = []
    for idx, task in enumerate(tasks):
        task_id = str(task.get("task_id", task.get("id", f"task_{idx}")))
        grading = task.get("grading", {})
        score = grading.get("mean") if isinstance(grading, dict) else None
        max_score = None
        if isinstance(grading, dict):
            runs = grading.get("runs", [])
            if runs and isinstance(runs, list) and isinstance(runs[0], dict):
                max_score = runs[0].get("max_score")
        if score is None:
            score = task.get("score")
        if max_score is None:
            max_score = task.get("max_score", task.get("maxScore"))

        raw_status = str(task.get("status") or "").lower()
        final_status = normalize_task_status(raw_status or "pending")
        # Preserve benchmark task status when it is present. A partial score is
        # still a valid graded result and should not be marked failed solely
        # because it is below max_score.
        if not raw_status and score is not None and max_score is not None and max_score > 0:
            final_status = "succeeded" if score >= max_score else "scored"

        result_json: dict[str, Any] = dict(task) if isinstance(task, dict) else {}
        # Inject templateName/templateVersion into resultJson
        mapping = task_id_map.get(task_id)
        if not mapping:
            mapping = template_name_map.get(task_id)
        if mapping:
            result_json["templateName"] = mapping["templateName"]
            result_json["templateTaskId"] = mapping["taskId"]
            if mapping.get("templateVersion") is not None:
                result_json["templateVersion"] = mapping["templateVersion"]
        elif task_id_map or template_name_map:
            print(f"[clawmind_adapter] Warning: no template mapping for taskId={task_id}", file=sys.stderr)

        item: dict[str, Any] = {
            "resultId": f"res_{bench_run_id}_{task_id}",
            "taskId": task_id,
            "taskName": task.get("name", task.get("task_name", task_id)),
            "status": final_status,
            "gradingType": "automated",
            "resultJson": result_json,
        }
        if score is not None:
            item["score"] = score
        if max_score is not None:
            item["maxScore"] = max_score
        if raw_status != final_status:
            item["notes"] = f"rawStatus={raw_status}, finalStatus={final_status} by grading score"
        execution_time = task_execution_time_ms(task)
        if execution_time is not None:
            item["executionTimeMs"] = execution_time
        if task.get("workspace"):
            item["workspacePath"] = task["workspace"]
        results.append(item)

    scores = [r["score"] for r in results if r.get("score") is not None]
    max_scores = [r["maxScore"] for r in results if r.get("maxScore") is not None]
    total_score = sum(scores) if scores else None
    total_max = sum(max_scores) if max_scores else None
    pass_rate = (total_score / total_max) if total_score is not None and total_max else None

    results_uploaded = False
    if results:
        if api_ok("POST", f"{clawweb_url}/api/bench/runs/{bench_run_id}/results", {"results": results}):
            results_uploaded = True
            adapter_log(f"upload-results uploaded task results count={len(results)}", output_dir)
        else:
            adapter_log(f"upload-results failed to upload task results count={len(results)}", output_dir)

    # ── Upload session artifacts (transcript JSONL files) ──
    # Build task_usage_map from report tasks
    task_usage_map: dict[str, Any] = {}
    for task in tasks:
        task_id = str(task.get("task_id", task.get("id", "")))
        if isinstance(task.get("usage"), dict):
            task_usage_map[task_id] = task["usage"]

    transcript_uploaded = 0
    transcript_skipped = 0
    if bench_run_id:
        transcript_uploaded, transcript_skipped = upload_transcript_artifacts(
            clawweb_url=clawweb_url,
            bench_run_id=bench_run_id,
            output_dir=output_dir,
            scene=scene,
            agentbench_home=agentbench_home,
            task_usage_map=task_usage_map,
        )

    succeeded_count = sum(1 for r in results if r.get("status") == "succeeded")
    failed_count = sum(1 for r in results if r.get("status") == "failed")
    merged_summary: dict[str, Any] = {}
    if bench_run_id:
        try:
            run = api_json("GET", f"{clawweb_url}/api/bench/runs/{bench_run_id}")
            raw_summary = run.get("summary") if isinstance(run, dict) else {}
            if isinstance(raw_summary, dict):
                merged_summary.update(raw_summary)
        except Exception as err:
            print(f"[clawmind_adapter] upload-results warning fetching run summary: {err}", file=sys.stderr)

    merged_summary.update({
        "taskCount": len(results),
        "succeededCount": succeeded_count,
        "failedCount": failed_count,
    })
    if results and results_uploaded:
        run_status = "succeeded"
    elif results and not results_uploaded:
        run_status = "failed"
    else:
        run_status = status
    if status != "succeeded" and run_status == "succeeded":
        merged_summary["runnerStatus"] = status
        if error_text:
            merged_summary["runnerError"] = error_text
        merged_summary["runnerWarning"] = "Runner returned non-zero after producing a valid report"
    final_progress: dict[str, Any] = {}
    raw_progress = merged_summary.get("progress")
    if isinstance(raw_progress, dict):
        final_progress.update(raw_progress)
    final_progress.update({
        "phase": "completed" if run_status == "succeeded" else run_status,
        "taskTotal": len(results),
        "taskCompleted": len(results),
        "taskSucceeded": succeeded_count,
        "taskFailed": failed_count,
        "scoreSoFar": total_score,
        "maxScoreSoFar": total_max,
        "passRateSoFar": pass_rate,
        "lastUpdatedAt": int(time.time()),
    })
    efficiency = report.get("efficiency") if isinstance(report, dict) else None
    if isinstance(efficiency, dict):
        final_progress["tokenUsageSoFar"] = {
            "inputTokens": efficiency.get("total_input_tokens", 0),
            "outputTokens": efficiency.get("total_output_tokens", 0),
            "totalTokens": efficiency.get("total_tokens", 0),
        }
    merged_summary["progress"] = final_progress

    run_payload: dict[str, Any] = {
        "status": run_status,
        "summary": merged_summary,
    }
    if started_at:
        run_payload["startedAt"] = int(started_at)
    if completed_at:
        run_payload["completedAt"] = int(completed_at)
    elif run_status in {"succeeded", "failed", "cancelled"}:
        run_payload["completedAt"] = int(time.time())
    if total_score is not None:
        run_payload["score"] = total_score
    if total_max is not None:
        run_payload["maxScore"] = total_max
    if pass_rate is not None:
        run_payload["passRate"] = pass_rate
    if error_text:
        run_payload["errorText"] = error_text

    if api_ok("PUT", f"{clawweb_url}/api/bench/runs/{bench_run_id}", run_payload):
        adapter_log(
            "upload-results updated run "
            f"benchRunId={bench_run_id} status={run_status} runnerStatus={status} score={total_score} maxScore={total_max} "
            f"passRate={pass_rate} sessionUploaded={transcript_uploaded} sessionSkipped={transcript_skipped}",
            output_dir,
        )
    else:
        adapter_log(f"upload-results failed to update run benchRunId={bench_run_id}", output_dir)
    json_out(
        {
            "status": "ok",
            "benchRunId": bench_run_id,
            "taskCount": len(results),
            "score": total_score,
            "maxScore": total_max,
            "passRate": pass_rate,
            "sessionArtifactsUploaded": transcript_uploaded,
            "sessionArtifactsSkipped": transcript_skipped,
        }
    )


def _compact_summary(report_context: dict[str, Any], max_chars: int = 6000) -> str:
    """Build a compact prompt summary under max_chars for subagent fallback."""
    run_meta = {
        "benchRunId": report_context.get("benchRunId"),
        "domainId": report_context.get("domainId"),
        "runScope": report_context.get("runScope"),
        "templateCount": report_context.get("templateCount"),
        "templates": report_context.get("templates"),
        "templateName": report_context.get("templateName"),
        "templateVersion": report_context.get("templateVersion"),
        "model": report_context.get("model"),
        "suite": report_context.get("suite"),
        "scene": report_context.get("scene"),
        "score": report_context.get("score"),
        "maxScore": report_context.get("maxScore"),
        "passRate": report_context.get("passRate"),
        "errorText": report_context.get("errorText"),
        "runStamp": report_context.get("runStamp"),
        "outputDir": report_context.get("outputDir"),
    }
    results = report_context.get("resultsSummary", [])
    compact_results = []
    for r in results:
        item: dict[str, Any] = {
            "taskId": r.get("taskId"),
            "taskName": r.get("taskName"),
            "status": r.get("status"),
            "score": r.get("score"),
            "maxScore": r.get("maxScore"),
        }
        if r.get("breakdown"):
            item["breakdown"] = r["breakdown"]
        if r.get("errorText"):
            item["errorText"] = r["errorText"]
        compact_results.append(item)

    report = report_context.get("benchmarkReport", {})
    tasks = report.get("tasks", []) if isinstance(report, dict) else []
    compact_tasks = []
    for t in tasks[:20]:
        grading = t.get("grading", {}) if isinstance(t, dict) else {}
        compact_tasks.append({
            "task_id": t.get("task_id") if isinstance(t, dict) else None,
            "name": t.get("name") if isinstance(t, dict) else None,
            "status": t.get("status") if isinstance(t, dict) else None,
            "score": grading.get("mean") if isinstance(grading, dict) else None,
        })

    payload = {
        "run": run_meta,
        "resultsSummary": compact_results,
        "taskCount": len(compact_tasks),
        "tasks": compact_tasks,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) > max_chars:
        text = text[:max_chars]
        text = text.rsplit("\n", 1)[0]
        text += "\n... (truncated)"
    return text


def action_prepare_report() -> None:
    bench_run_id = env("BENCH_RUN_ID")
    result_path = env("RESULT_PATH")
    task_file = env("TASK_FILE")
    domain_id = env("DOMAIN_ID")
    template_name = env("TEMPLATE_NAME")
    template_version = env("TEMPLATE_VERSION", "")
    run_stamp = env("RUN_STAMP", "")
    output_dir = env("OUTPUT_DIR", "")
    run_status = env("RUN_STATUS", "")
    run_error = env("RUN_ERROR", "")
    run_exit_code = env("RUN_EXIT_CODE", "")
    run_log_path = env("RUN_LOG_PATH", "")
    run_log_tail = env("RUN_LOG_TAIL", "")
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")
    report_template_path = config_value("REPORT_TEMPLATE_PATH", OVERRIDE_REPORT_TEMPLATE_PATH, required=False)
    scene = env("SCENE")
    agentbench_home = Path(config_value("AGENTBENCH_HOME", OVERRIDE_AGENTBENCH_HOME))

    if not bench_run_id:
        fail("BENCH_RUN_ID is required")

    # Resolve result path
    if not result_path or not Path(result_path).is_file():
        if output_dir:
            result_path = find_report(output_dir, scene, cwd=agentbench_home)

    # Read benchmark report
    report: dict[str, Any] = {}
    if result_path and Path(result_path).is_file():
        try:
            report = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except Exception as exc:
            json_out({"status": "failed", "error": f"Failed to read report: {exc}", "benchRunId": bench_run_id})

    # Read task markdown
    task_md = ""
    task_path = Path(task_file) if task_file else None
    if (not task_path or not task_path.is_file()) and output_dir:
        input_dir = Path(output_dir).parent / "input"
        if input_dir.is_dir():
            task_candidates = sorted(input_dir.glob("*.md"))
            task_path = task_candidates[0] if task_candidates else task_path
    if task_path and task_path.is_file():
        try:
            task_md = task_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Read report template
    template_md = ""
    template_path = Path(report_template_path) if report_template_path else None
    if template_path and template_path.is_file():
        try:
            template_md = template_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Build report context from local report + optional ClawWeb API
    report_context: dict[str, Any] = {
        "benchRunId": bench_run_id,
        "reportTemplatePath": str(template_path) if template_path else "",
        "domainId": domain_id,
        "resultPath": result_path,
        "runStamp": run_stamp,
        "outputDir": output_dir,
        "runStatus": run_status,
        "runError": run_error,
        "runExitCode": run_exit_code,
        "runLogPath": run_log_path,
        "runLogTail": run_log_tail,
    }

    # Add template info if available (template run)
    if template_name:
        report_context["templateName"] = template_name
    if template_version:
        report_context["templateVersion"] = int(template_version)

    # Pull run details from ClawWeb API to get runScope/templateCount/templates
    try:
        run = api_json("GET", f"{clawweb_url}/api/bench/runs/{bench_run_id}")
        run_config = run.get("runConfig") or {}
        if isinstance(run_config, str):
            try:
                run_config = json.loads(run_config)
            except Exception:
                run_config = {}

        report_context["runScope"] = run_config.get("runScope") or run.get("runScope") or "template"
        report_context["templateCount"] = run_config.get("templateCount") or run.get("templateCount")
        report_context["templates"] = run_config.get("templates") or run.get("templates")
        report_context.update({
            "model": run.get("model"),
            "suite": run.get("suite"),
            "scene": run.get("scene"),
            "score": run.get("score"),
            "maxScore": run.get("maxScore"),
            "passRate": run.get("passRate"),
            "startedAt": run.get("startedAt"),
            "completedAt": run.get("completedAt"),
            "errorText": run.get("errorText"),
        })
    except Exception as err:
        print(f"[clawmind_adapter] prepare-report warning fetching run: {err}", file=sys.stderr)

    try:
        results_resp = api_json("GET", f"{clawweb_url}/api/bench/runs/{bench_run_id}/results")
        results = results_resp if isinstance(results_resp, list) else results_resp.get("results", [])
        results_summary: list[dict[str, Any]] = []
        for r in results:
            item: dict[str, Any] = {
                "taskId": r.get("taskId"),
                "taskName": r.get("taskName"),
                "status": r.get("status"),
                "score": r.get("score"),
                "maxScore": r.get("maxScore"),
                "gradingType": r.get("gradingType"),
            }
            result_json = r.get("resultJson")
            if isinstance(result_json, dict):
                grading = result_json.get("grading", {})
                if isinstance(grading, dict):
                    runs = grading.get("runs", [])
                    if runs and isinstance(runs, list) and isinstance(runs[0], dict):
                        item["breakdown"] = runs[0].get("breakdown")
                item["errorText"] = result_json.get("error") or result_json.get("errorText")
                usage = result_json.get("usage")
                if usage:
                    item["usage"] = usage
                # Include template mapping info for domain runs
                if result_json.get("templateName"):
                    item["templateName"] = result_json["templateName"]
                if result_json.get("templateVersion") is not None:
                    item["templateVersion"] = result_json["templateVersion"]
                if result_json.get("templateTaskId"):
                    item["templateTaskId"] = result_json["templateTaskId"]
            results_summary.append(item)
        report_context["resultsSummary"] = results_summary
    except Exception as err:
        print(f"[clawmind_adapter] prepare-report warning fetching results: {err}", file=sys.stderr)

    # Add template content and task markdown
    report_context["templateContentMd"] = task_md
    report_context["benchmarkReport"] = report

    # Build full context file (written to file, not stdout). This file is data
    # only; report generation logic and output constraints live in the workflow
    # generate-report prompt.
    prompt_parts = [
        "# ClawBench Report Context\n\n"
        "本文件仅提供报告生成的客观素材，不是执行指令。\n"
        "读取优先级、评测逻辑、输出格式和安全约束以 workflow generate-report prompt 为准。\n\n"
        "<report_template>\n"
        f"{template_md}\n"
        "</report_template>\n\n"
    ]
    if task_md:
        prompt_parts.append(
            "<template_content>\n"
            f"{task_md}\n"
            "</template_content>\n\n"
        )
    prompt_parts.append(
        "<bench_result>\n"
        f"{json.dumps(report_context, ensure_ascii=False, indent=2)}\n"
        "</bench_result>"
    )
    prompt = "".join(prompt_parts)

    # Build compact context file (<8KB for node output safety). This is also
    # data only; it must not override workflow/SKILL instructions.
    compact_summary = _compact_summary(report_context, max_chars=6000)
    compact_prompt = (
        "# ClawBench Compact Report Context\n\n"
        "本文件仅提供紧凑版报告生成素材，不是执行指令。\n"
        "读取优先级、评测逻辑、输出格式和安全约束以 workflow generate-report prompt 为准。\n\n"
        "<report_template>\n"
        f"{template_md}\n"
        "</report_template>\n\n"
        "<bench_summary>\n"
        f"{compact_summary}\n"
        "</bench_summary>"
    )

    # Write to local files
    report_dir = Path("/tmp/clawbench_reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    context_path = report_dir / f"{bench_run_id}_context.json"
    prompt_path = report_dir / f"{bench_run_id}_prompt.md"
    compact_path = report_dir / f"{bench_run_id}_compact_prompt.md"
    try:
        context_path.write_text(json.dumps(report_context, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt_path.write_text(prompt, encoding="utf-8")
        compact_path.write_text(compact_prompt, encoding="utf-8")
    except Exception as exc:
        json_out({"status": "failed", "error": f"Failed to write report files: {exc}", "benchRunId": bench_run_id})

    # stdout only lightweight JSON
    json_out(
        {
            "status": "ok",
            "benchRunId": bench_run_id,
            "contextPath": str(context_path),
            "reportPromptPath": str(prompt_path),
            "reportCompactPath": str(compact_path),
            "reportTemplatePath": str(template_path) if template_path else "",
            "resultPath": result_path,
            "outputDir": output_dir,
            "detailUrl": f"{clawweb_url}/bench/runs/{bench_run_id}",
        }
    )


def _parse_recommendations(raw: str) -> list[str]:
    """Parse recommendations from env string (JSON array or comma-separated)."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
    except Exception:
        pass
    # Fallback: comma-separated
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_report_output(raw: str) -> dict[str, Any]:
    """Parse generate-report output when workflow outputContract did not project fields."""
    if not raw:
        return {}

    candidates: list[str] = []
    text = raw.strip()
    candidates.append(text)

    if text.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
        candidates.append(stripped)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed: Any = json.loads(candidate)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def action_summarize() -> None:
    bench_run_id = env("BENCH_RUN_ID")
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")
    status = env("STATUS", "unknown")
    detail_url = env("DETAIL_URL")
    result_path = env("RESULT_PATH")
    report_markdown = env("REPORT_MARKDOWN", "")
    report_summary = env("REPORT_SUMMARY", "")
    report_risk_level = env("REPORT_RISK_LEVEL", "")
    report_recommendations = _parse_recommendations(env("REPORT_RECOMMENDATIONS", ""))
    report_error = env("REPORT_ERROR", "")
    report_output = env("REPORT_OUTPUT", "")
    report_child_session_key = env("REPORT_CHILD_SESSION_KEY", "")
    report_prompt_path = env("REPORT_PROMPT_PATH", "")
    report_compact_path = env("REPORT_COMPACT_PATH", "")

    if not report_markdown and report_output:
        parsed_report = _parse_report_output(report_output)
        parsed_markdown = parsed_report.get("reportMarkdown")
        if isinstance(parsed_markdown, str) and parsed_markdown.strip():
            report_markdown = parsed_markdown
            if not report_summary and isinstance(parsed_report.get("reportSummary"), str):
                report_summary = str(parsed_report.get("reportSummary") or "")
            if not report_risk_level and isinstance(parsed_report.get("riskLevel"), str):
                report_risk_level = str(parsed_report.get("riskLevel") or "")
            if not report_recommendations and isinstance(parsed_report.get("recommendations"), list):
                report_recommendations = [str(x) for x in parsed_report.get("recommendations", []) if x]
        elif report_output.lstrip().startswith("#"):
            # The report agent's current contract is plain Markdown.  Keep
            # legacy JSON parsing above for older workflow definitions, but do
            # not require long Markdown content to survive JSON escaping.
            report_markdown = report_output.strip()

    if not bench_run_id:
        json_out({
            "status": "failed",
            "error": "BENCH_RUN_ID is required",
            "finishedMessage": "ClawBench run finished (missing benchRunId)",
        })

    # Pull normalized data from ClawWeb for finishedMessage
    domain_id = ""
    template_name = ""
    run_scope = "template"
    template_count = None
    score = None
    max_score = None
    existing_summary: dict[str, Any] | None = None
    try:
        run = api_json("GET", f"{clawweb_url}/api/bench/runs/{bench_run_id}")
        domain_id = run.get("domainId") or ""
        template_name = run.get("templateName") or ""
        run_scope = run.get("runScope") or "template"
        template_count = run.get("templateCount")
        score = run.get("score")
        max_score = run.get("maxScore")
        raw_summary = run.get("summary")
        if isinstance(raw_summary, dict):
            existing_summary = raw_summary
    except Exception as err:
        print(f"[clawmind_adapter] summarize warning fetching run: {err}", file=sys.stderr)

    # Persist report into bench run summary so ClawWeb can display it
    report_saved = False
    report_fallback_saved = False
    fallback_report_error = report_error or "generate-report did not return reportMarkdown"
    if report_markdown and bench_run_id:
        merged_summary = existing_summary.copy() if existing_summary else {}
        for key in (
            "reportError",
            "reportRawOutput",
            "reportChildSessionKey",
            "reportPromptPath",
            "reportCompactPath",
        ):
            merged_summary.pop(key, None)
        merged_summary["reportMarkdown"] = report_markdown
        if report_summary:
            merged_summary["reportSummary"] = report_summary
        if report_risk_level:
            merged_summary["reportRiskLevel"] = report_risk_level
        if report_recommendations:
            merged_summary["reportRecommendations"] = report_recommendations
        if api_ok(
            "PUT",
            f"{clawweb_url}/api/bench/runs/{bench_run_id}",
            {"summary": merged_summary},
        ):
            report_saved = True
    elif (report_error or report_output or report_child_session_key or report_prompt_path or report_compact_path) and bench_run_id:
        # Report generation failed; record failure info in summary
        merged_summary = existing_summary.copy() if existing_summary else {}
        for key in (
            "reportMarkdown",
            "reportSummary",
            "reportRiskLevel",
            "reportRecommendations",
        ):
            merged_summary.pop(key, None)
        merged_summary["reportError"] = fallback_report_error
        if report_output:
            merged_summary["reportRawOutput"] = report_output
        if report_child_session_key:
            merged_summary["reportChildSessionKey"] = report_child_session_key
        if report_prompt_path:
            merged_summary["reportPromptPath"] = report_prompt_path
        if report_compact_path:
            merged_summary["reportCompactPath"] = report_compact_path
        if api_ok(
            "PUT",
            f"{clawweb_url}/api/bench/runs/{bench_run_id}",
            {"summary": merged_summary},
        ):
            report_fallback_saved = True

    # Build finishedMessage (small; do NOT include full reportMarkdown)
    if run_scope == "domain":
        parts = [f"ClawBench domain run finished: {domain_id} ({template_count or '?'} templates)" if domain_id else "ClawBench domain run finished"]
    else:
        parts = [f"ClawBench run finished: {domain_id}/{template_name}" if domain_id and template_name else "ClawBench run finished"]
    parts.append(f"- Status: {status}")
    if score is not None and max_score is not None:
        parts.append(f"- Score: {score} / {max_score}")
    report_url = detail_url or (f"{clawweb_url}/bench/runs/{bench_run_id}" if bench_run_id else "")
    if report_url:
        parts.append(f"- Report URL: {report_url}")

    if report_saved:
        parts.append("- Report: generated and saved to bench run summary")
    elif report_fallback_saved:
        parts.append("- Report: raw generate-report output saved to bench run summary")

    finished_message = "\n".join(parts)

    # Output JSON: keep finishedMessage short to avoid OpenClaw chat truncation.
    output: dict[str, Any] = {
        "summary": "Benchmark completed",
        "status": status,
        "benchRunId": bench_run_id,
        "detailUrl": detail_url,
        "resultPath": result_path,
        "finishedMessage": finished_message,
        "reportSaved": report_saved,
        "reportFallbackSaved": report_fallback_saved,
    }
    if report_fallback_saved:
        output["reportError"] = fallback_report_error

    json_out(output)


def action_resolve_report() -> None:
    bench_run_id = env("BENCH_RUN_ID") or env("BENCH_ID")
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")
    agentbench_home = Path(config_value("AGENTBENCH_HOME", OVERRIDE_AGENTBENCH_HOME))
    output_dir = env("OUTPUT_DIR")
    result_path = env("RESULT_PATH")
    log_stderr(
        "resolve-report started "
        f"benchRunId={bench_run_id or '-'} outputDir={output_dir or '<auto>'} "
        f"resultPath={result_path or '<auto>'}"
    )

    if not bench_run_id:
        json_out({"status": "failed", "error": "benchId is required"}, 1)

    run: dict[str, Any] = {}
    try:
        raw_run = api_json("GET", f"{clawweb_url}/api/bench/runs/{bench_run_id}")
        if isinstance(raw_run, dict):
            run = raw_run
    except Exception as err:
        json_out({"status": "failed", "benchRunId": bench_run_id, "error": f"Failed to fetch bench run: {err}"}, 1)

    if not output_dir:
        output_dir = str(workspace_clawbench_result_root(bench_run_id) / "output")

    scene = str(run.get("scene") or env("SCENE", "openclaw-clawbench"))
    if not result_path or not Path(result_path).is_file():
        result_path = find_report(output_dir, scene, cwd=agentbench_home)
    if not result_path or not Path(result_path).is_file():
        json_out({
            "status": "failed",
            "benchRunId": bench_run_id,
            "outputDir": output_dir,
            "scene": scene,
            "error": "No benchmark report found",
        }, 1)

    run_config = run.get("runConfig") or {}
    if isinstance(run_config, str):
        try:
            run_config = json.loads(run_config)
        except Exception:
            run_config = {}
    if not isinstance(run_config, dict):
        run_config = {}

    template_name = str(run.get("templateName") or "")
    template_version = run.get("templateVersion")
    run_scope = str(run_config.get("runScope") or ("domain" if template_name == "__domain__" else "template"))

    log_stderr(
        "resolve-report completed "
        f"benchRunId={bench_run_id} outputDir={output_dir} resultPath={result_path}"
    )
    json_out({
        "status": "ok",
        "benchRunId": bench_run_id,
        "detailUrl": f"{clawweb_url}/bench/runs/{bench_run_id}",
        "outputDir": output_dir,
        "resultPath": result_path,
        "domainId": str(run.get("domainId") or ""),
        "templateName": "" if template_name == "__domain__" else template_name,
        "templateVersion": template_version if template_version is not None else "",
        "runScope": run_scope,
        "templateCount": run_config.get("templateCount") or "",
        "runStamp": run_config.get("runStamp") or "",
        "scene": scene,
        "model": run.get("model") or "",
        "suite": run.get("suite") or "",
        "runStatus": run.get("status") or "",
        "errorText": run.get("errorText") or "",
    })


def action_sync_sessions() -> None:
    bench_run_id = env("BENCH_RUN_ID") or env("BENCH_ID")
    clawweb_url = config_value("CLAWWEB_URL", OVERRIDE_CLAWWEB_URL).rstrip("/")
    agentbench_home = Path(config_value("AGENTBENCH_HOME", OVERRIDE_AGENTBENCH_HOME))
    output_dir = env("OUTPUT_DIR")
    scene = env("SCENE", "openclaw-clawbench")
    result_path = env("RESULT_PATH")

    if not bench_run_id:
        json_out({"status": "failed", "error": "benchId is required"}, 1)
    if not output_dir:
        output_dir = str(workspace_clawbench_result_root(bench_run_id) / "output")

    adapter_log(
        "sync-sessions started "
        f"benchRunId={bench_run_id} outputDir={output_dir} scene={scene}",
        output_dir,
    )

    if not result_path or not Path(result_path).is_file():
        result_path = find_report(output_dir, scene, cwd=agentbench_home)

    task_usage_map: dict[str, Any] = {}
    if result_path and Path(result_path).is_file():
        try:
            report = json.loads(Path(result_path).read_text(encoding="utf-8"))
            tasks = report.get("tasks", []) if isinstance(report, dict) else []
            if not tasks and isinstance(report, dict) and ("task_id" in report or "id" in report):
                tasks = [report]
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("task_id", task.get("id", "")))
                usage = task.get("usage")
                if task_id and isinstance(usage, dict):
                    task_usage_map[task_id] = usage
        except Exception as exc:
            adapter_log(f"sync-sessions warning failed to read report usage: {exc}", output_dir)

    uploaded, skipped = upload_transcript_artifacts(
        clawweb_url=clawweb_url,
        bench_run_id=bench_run_id,
        output_dir=output_dir,
        scene=scene,
        agentbench_home=agentbench_home,
        task_usage_map=task_usage_map,
    )
    adapter_log(
        "sync-sessions completed "
        f"benchRunId={bench_run_id} uploaded={uploaded} skipped={skipped}",
        output_dir,
    )
    json_out({
        "status": "ok",
        "benchRunId": bench_run_id,
        "outputDir": output_dir,
        "resultPath": result_path,
        "sessionArtifactsUploaded": uploaded,
        "sessionArtifactsSkipped": skipped,
    })

# --- restored from master_clawbench (report-repair line preserved) ---
def log_stderr(message: str) -> None:
    print(f"[clawmind_adapter] {message}", file=sys.stderr)


def _retry_delay(attempt: int) -> float:
    jitter = random.uniform(0, 0.25)
    return API_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1)) + jitter


def find_template_content_md(template: dict[str, Any], target_version: int) -> str:
    for version in template.get("versions", []):
        if int(version.get("version", 0)) == target_version:
            return str(version.get("contentMd", "") or "")
    return ""

ACTIONS = {
    "load-template": action_load_template,
    "create-run": action_create_run,
    "run-agentbench": action_run_agentbench,
    "upload-results": action_upload_results,
    "resolve-report": action_resolve_report,
    "sync-sessions": action_sync_sessions,
    "prepare-report": action_prepare_report,
    "summarize": action_summarize,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="ClawBench ClawMind adapter")
    parser.add_argument("action", choices=sorted(ACTIONS))
    args = parser.parse_args()
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    # Resolve OWNER_ID early so all API calls include X-User-Id.
    get_owner_user_id()
    try:
        ACTIONS[args.action]()
    except Exception as err:
        fail(str(err))


if __name__ == "__main__":
    main()
