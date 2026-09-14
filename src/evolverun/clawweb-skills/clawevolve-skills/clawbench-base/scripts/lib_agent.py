"""
OpenClaw agent execution helpers for PinchBench.
"""

from __future__ import annotations

import json
import fnmatch
import logging
import os
import platform
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request

from lib_openclaw_cli import run_openclaw_agent
from lib_tasks import Task
from lib_evolve_identity import task_scoped_agent_id


logger = logging.getLogger(__name__)

USE_SHELL = platform.system() == "Windows"
TRANSCRIPT_EXACT_LOOKUP_ATTEMPTS = 30
IGNORE_PATTERNS = [
    "*.swp",
    "*.swo",
    "*.swn",
    ".*.swp",
    "*~",
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.i?????",
]


class ModelValidationError(Exception):
    """Raised when a model ID is invalid or inaccessible."""

    pass


class OpenClawAgentCreationError(RuntimeError):
    """Raised when a benchmark agent cannot be created or verified."""

    pass


MAX_OPENCLAW_MESSAGE_CHARS = int(os.environ.get("PINCHBENCH_MAX_MSG_CHARS", "8000"))
JUDGE_MAX_MSG_CHARS = int(os.environ.get("PINCHBENCH_JUDGE_MAX_MSG_CHARS", "30000"))
DEFAULT_INTERACTION_JUDGE_MODEL = "antchat/GLM-5.1"
DEFAULT_INTERACTION_JUDGE_AGENT_PREFIX = "bench-judge"
INTERACTION_PREVIEW_CHARS = int(os.environ.get("CLAWBENCH_INTERACTION_PREVIEW_CHARS", "1200"))
OPENCLAW_AGENTS_LIST_TIMEOUT_SECONDS = float(os.environ.get("CLAWBENCH_OPENCLAW_AGENTS_LIST_TIMEOUT_SECONDS", "20"))
OPENCLAW_AGENTS_ADD_FIRST_TIMEOUT_SECONDS = float(os.environ.get("CLAWBENCH_OPENCLAW_AGENTS_ADD_FIRST_TIMEOUT_SECONDS", "60"))
OPENCLAW_AGENTS_ADD_RETRY_TIMEOUT_SECONDS = float(os.environ.get("CLAWBENCH_OPENCLAW_AGENTS_ADD_RETRY_TIMEOUT_SECONDS", "120"))
OPENCLAW_AGENTS_DELETE_TIMEOUT_SECONDS = float(os.environ.get("CLAWBENCH_OPENCLAW_AGENTS_DELETE_TIMEOUT_SECONDS", "30"))


def is_temp_file(path: str | os.PathLike[str]) -> bool:
    name = os.path.basename(os.fspath(path))
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORE_PATTERNS)


def ignore_temp_files(dir_path: str, names: list[str]) -> set[str]:
    return {name for name in names if is_temp_file(name)}


def safe_copy2(src: str, dst: str) -> str:
    import shutil

    if is_temp_file(src):
        logger.warning("[skip temp file] %s", src)
        return dst

    try:
        return shutil.copy2(src, dst)
    except FileNotFoundError:
        logger.warning("[skip vanished file] %s", src)
        return dst
    except PermissionError:
        logger.warning("[skip permission denied] %s", src)
        return dst
    except OSError as exc:
        logger.warning("[skip copy error] %s: %s", src, exc)
        return dst


def safe_copytree(skill_dir_src: Path, dest_skill_dir: Path) -> bool:
    import shutil

    try:
        shutil.copytree(
            skill_dir_src,
            dest_skill_dir,
            ignore=ignore_temp_files,
            copy_function=safe_copy2,
            dirs_exist_ok=True,
        )
        return True
    except FileNotFoundError:
        logger.warning("[skip copytree] source not found: %s", skill_dir_src)
        return False
    except PermissionError:
        logger.warning(
            "[skip copytree] permission denied: %s -> %s",
            skill_dir_src,
            dest_skill_dir,
        )
        return False
    except shutil.Error as exc:
        logger.warning(
            "[skip copytree] partial copy errors: %s -> %s, error=%s",
            skill_dir_src,
            dest_skill_dir,
            exc,
        )
        return False
    except OSError as exc:
        logger.warning(
            "[skip copytree] os error: %s -> %s, error=%s",
            skill_dir_src,
            dest_skill_dir,
            exc,
        )
        return False


def _coerce_subprocess_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _preview_subprocess_output(value: Any, max_chars: int = 2000) -> str:
    text = _coerce_subprocess_output(value)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... <truncated {len(text) - max_chars} chars>"


def slugify_model(model_id: str) -> str:
    return model_id.replace("/", "-").replace(".", "-").lower()


def validate_openrouter_model(model_id: str, timeout_seconds: float = 10.0) -> bool:
    """
    Validate that a model ID exists on OpenRouter.

    Args:
        model_id: Model ID (with or without openrouter/ prefix)
        timeout_seconds: HTTP request timeout

    Returns:
        True if model is valid and accessible

    Raises:
        ModelValidationError: If model doesn't exist or validation fails
    """
    # Strip openrouter/ prefix if present
    bare_model_id = model_id
    if bare_model_id.startswith("openrouter/"):
        bare_model_id = bare_model_id[len("openrouter/") :]

    # Skip validation for non-OpenRouter models
    if "/" not in bare_model_id:
        logger.info("Skipping model validation for non-OpenRouter model: %s", model_id)
        return True

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set, skipping model validation")
        return True

    logger.info("🔍 Validating model: %s", bare_model_id)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://pinchbench.com",
        "X-Title": "PinchBench",
    }

    # First, try the specific model endpoint (fast path for valid models)
    encoded_model_id = bare_model_id.replace("/", "%2F")
    specific_endpoint = f"https://openrouter.ai/api/v1/models/{encoded_model_id}"
    req = request.Request(specific_endpoint, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            # Model exists - validation passed
            logger.info("✅ Model validated: %s", bare_model_id)
            return True
    except error.HTTPError as exc:
        if exc.code == 404:
            # Model not found - fall through to fetch full catalog for suggestions
            pass
        else:
            logger.warning("OpenRouter API error during validation: %s", exc)
            return True
    except error.URLError as exc:
        logger.warning("Network error during model validation: %s", exc)
        return True

    # Model not found - fetch full catalog for "did you mean" suggestions
    catalog_endpoint = "https://openrouter.ai/api/v1/models"
    req = request.Request(catalog_endpoint, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        logger.warning("OpenRouter API error fetching model catalog: %s", exc)
        raise ModelValidationError(f"Model '{bare_model_id}' not found on OpenRouter.")
    except error.URLError as exc:
        logger.warning("Network error fetching model catalog: %s", exc)
        raise ModelValidationError(f"Model '{bare_model_id}' not found on OpenRouter.")
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse OpenRouter response: %s", exc)
        raise ModelValidationError(f"Model '{bare_model_id}' not found on OpenRouter.")

    models = data.get("data", [])
    model_ids = {
        mid
        for m in models
        if isinstance(m, dict)
        for mid in [m.get("id")]
        if isinstance(mid, str) and mid
    }

    # Some OpenRouter model detail lookups intermittently return 404 for valid
    # IDs. Treat an exact catalog hit as authoritative to avoid false negatives.
    if bare_model_id in model_ids:
        logger.info("✅ Model validated via catalog fallback: %s", bare_model_id)
        return True

    # Check for close matches (typos)
    close_matches = []
    bare_lower = bare_model_id.lower()
    for mid in model_ids:
        mid_lower = mid.lower()
        if mid_lower == bare_lower:
            continue
        if bare_lower in mid_lower or mid_lower in bare_lower:
            close_matches.append(mid)

    error_msg = f"Model '{bare_model_id}' not found on OpenRouter."
    if close_matches:
        close_matches_str = ", ".join(sorted(close_matches)[:5])
        error_msg += f" Did you mean: {close_matches_str}?"
    else:
        # Try to suggest based on provider
        provider = bare_model_id.split("/")[0] if "/" in bare_model_id else None
        if provider:
            provider_models = [m for m in model_ids if m.startswith(f"{provider}/")]
            if provider_models:
                error_msg += (
                    f" Available {provider} models: {', '.join(sorted(provider_models)[:5])}"
                )

    raise ModelValidationError(error_msg)


def _run_openclaw_agents_list(timeout_seconds: float = OPENCLAW_AGENTS_LIST_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["openclaw", "agents", "list"],
            capture_output=True,
            text=True,
            check=False,
            shell=USE_SHELL,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        logger.error("openclaw CLI not found while listing agents")
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "OpenClaw agents list timed out after %.1fs; stdout=%s stderr=%s",
            timeout_seconds,
            _preview_subprocess_output(exc.stdout, 2000),
            _preview_subprocess_output(exc.stderr, 2000),
        )
    return None


def _parse_openclaw_agents(stdout: str) -> tuple[set[str], dict[str, Path]]:
    existing_agents: set[str] = set()
    workspaces: dict[str, Path] = {}
    current_agent: str | None = None
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            name_part = stripped[2:].split()[0] if stripped[2:].strip() else ""
            current_agent = name_part.lower() if name_part else None
            if current_agent:
                existing_agents.add(current_agent)
            continue
        if current_agent and "Workspace:" in line:
            workspace_str = line.split("Workspace:", 1)[1].strip()
            if workspace_str.startswith("~/"):
                workspace_str = str(Path.home() / workspace_str[2:])
            workspaces[current_agent] = Path(workspace_str)
    return existing_agents, workspaces


def _agent_registered_from_list(
    agent_id: str,
    list_result: subprocess.CompletedProcess[str] | None,
    expected_workspace: Path | None = None,
) -> bool:
    if list_result is None or list_result.returncode != 0:
        return False
    existing_agents, workspaces = _parse_openclaw_agents(list_result.stdout)
    normalized_id = agent_id.replace(":", "-").lower()
    key = agent_id.lower() if agent_id.lower() in existing_agents else normalized_id
    if key not in existing_agents:
        return False
    if expected_workspace is None:
        return True
    current_workspace = workspaces.get(key)
    if current_workspace is None:
        logger.warning("Agent %s is listed but workspace is missing", agent_id)
        return False
    if current_workspace.resolve() != expected_workspace.resolve():
        logger.warning(
            "Agent %s is listed but workspace does not match: %s != %s",
            agent_id,
            current_workspace,
            expected_workspace,
        )
        return False
    return True


def _log_openclaw_result_preview(prefix: str, agent_id: str, result: subprocess.CompletedProcess[str]) -> None:
    stdout_preview = _preview_subprocess_output(result.stdout, 8000)
    stderr_preview = _preview_subprocess_output(result.stderr, 8000)
    if stdout_preview:
        logger.warning("%s stdout for %s:\n%s", prefix, agent_id, stdout_preview)
    if stderr_preview:
        logger.warning("%s stderr for %s:\n%s", prefix, agent_id, stderr_preview)


def _get_agent_workspace(agent_id: str) -> Path | None:
    """Get the workspace path for an agent from OpenClaw config."""
    try:
        list_result = _run_openclaw_agents_list()
        if list_result is None or list_result.returncode != 0:
            return None

        normalized_id = agent_id.replace(":", "-").lower()
        _, workspaces = _parse_openclaw_agents(list_result.stdout)
        return workspaces.get(agent_id.lower()) or workspaces.get(normalized_id)
    except Exception as exc:
        logger.warning("Failed to get agent workspace: %s", exc)
        return None


def _create_openclaw_agent_once(
    agent_id: str,
    model_id: str,
    workspace_dir: Path,
    timeout_seconds: float,
    attempt: int,
) -> bool:
    logger.info(
        "Creating OpenClaw agent %s (attempt=%s timeout=%.1fs workspace=%s)",
        agent_id,
        attempt,
        timeout_seconds,
        workspace_dir,
    )
    try:
        create_result = subprocess.run(
            [
                "openclaw",
                "agents",
                "add",
                agent_id,
                "--model",
                model_id,
                "--workspace",
                str(workspace_dir),
                "--non-interactive",
            ],
            capture_output=True,
            text=True,
            check=False,
            shell=USE_SHELL,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise OpenClawAgentCreationError(f"openclaw CLI not found while creating agent {agent_id}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "OpenClaw agent creation timed out after %.1fs for %s (attempt=%s); stdout=%s stderr=%s",
            timeout_seconds,
            agent_id,
            attempt,
            _preview_subprocess_output(exc.stdout, 8000),
            _preview_subprocess_output(exc.stderr, 8000),
        )
        return False

    if create_result.returncode == 0:
        logger.info("Agent creation returned 0 for %s (attempt=%s)", agent_id, attempt)
        stdout_preview = _preview_subprocess_output(create_result.stdout, 2000)
        stderr_preview = _preview_subprocess_output(create_result.stderr, 2000)
        if stdout_preview:
            logger.info("Agent creation stdout preview for %s (attempt=%s):\n%s", agent_id, attempt, stdout_preview)
        if stderr_preview:
            logger.info("Agent creation stderr preview for %s (attempt=%s):\n%s", agent_id, attempt, stderr_preview)
        return True

    logger.warning(
        "Agent creation returned %s for %s (attempt=%s)",
        create_result.returncode,
        agent_id,
        attempt,
    )
    _log_openclaw_result_preview("Agent creation", agent_id, create_result)
    return False


def _verify_agent_registered(agent_id: str, workspace_dir: Path, context: str) -> bool:
    verify_result = _run_openclaw_agents_list()
    agent_registered = _agent_registered_from_list(agent_id, verify_result, workspace_dir)
    logger.info(
        "Agent registry verification %s: agent=%s returncode=%s registered=%s workspace=%s",
        context,
        agent_id,
        verify_result.returncode if verify_result is not None else "timeout-or-error",
        agent_registered,
        workspace_dir,
    )
    if not agent_registered and verify_result is not None:
        _log_openclaw_result_preview(f"Agents list {context}", agent_id, verify_result)
    return agent_registered


def ensure_agent_exists(
    agent_id: str,
    model_id: str,
    workspace_dir: Path,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> bool:
    """Ensure the OpenClaw agent exists with the correct workspace.

    If the agent already exists but points to a different workspace, it is
    deleted and recreated so that the new workspace takes effect.

    When *base_url* is provided, a custom OpenAI-compatible provider is
    configured in the agent's ``models.json`` instead of relying on
    OpenRouter.  *api_key* defaults to ``${OPENAI_API_KEY}`` (resolved by
    OpenClaw at runtime) if not given.

    Returns True if the agent was (re)created.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)

    list_result = _run_openclaw_agents_list()

    if list_result is not None and list_result.returncode == 0:
        # Check for exact agent ID match — avoid substring false positives
        # (e.g. "bench-foo-4" matching "bench-foo-4-5" in the output).
        # Output format is "- <agent_id>" or "- <agent_id> (default)" per line.
        # OpenClaw normalizes colons to dashes in directory/display names, so
        # also check the normalized form.
        existing_agents, workspaces = _parse_openclaw_agents(list_result.stdout)
        normalized_id = agent_id.replace(":", "-").lower()
        agent_key = agent_id.lower() if agent_id.lower() in existing_agents else normalized_id
        if agent_key in existing_agents:
            # Agent exists — check workspace from the already-successful list call.
            current_workspace = workspaces.get(agent_key)
            if current_workspace is None:
                raise OpenClawAgentCreationError(
                    f"agent {agent_id} is listed but workspace is missing; refusing to delete/recreate on unknown workspace"
                )
            if current_workspace.resolve() == workspace_dir.resolve():
                logger.info("Agent %s already exists with correct workspace", agent_id)
                return False
            # Workspace is confirmed stale — delete and recreate.
            delete_name = agent_key
            logger.info(
                "Agent %s exists with stale workspace (%s != %s), recreating",
                agent_id,
                current_workspace,
                workspace_dir,
            )
            try:
                delete_result = subprocess.run(
                    ["openclaw", "agents", "delete", delete_name, "--force"],
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=USE_SHELL,
                    timeout=OPENCLAW_AGENTS_DELETE_TIMEOUT_SECONDS,
                )
                logger.info(
                    "Agent delete returned %s for %s",
                    delete_result.returncode,
                    delete_name,
                )
                if delete_result.returncode != 0:
                    _log_openclaw_result_preview("Agent delete", delete_name, delete_result)
                    raise OpenClawAgentCreationError(
                        f"failed to delete stale OpenClaw agent {delete_name}: exit {delete_result.returncode}"
                    )
            except FileNotFoundError as exc:
                raise OpenClawAgentCreationError(f"openclaw CLI not found while deleting agent {delete_name}: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                logger.warning(
                    "OpenClaw agent delete timed out after %.1fs for %s; stdout=%s stderr=%s",
                    OPENCLAW_AGENTS_DELETE_TIMEOUT_SECONDS,
                    delete_name,
                    _preview_subprocess_output(exc.stdout, 2000),
                    _preview_subprocess_output(exc.stderr, 2000),
                )
                raise OpenClawAgentCreationError(
                    f"timed out deleting stale OpenClaw agent {delete_name}"
                ) from exc

    first_ok = _create_openclaw_agent_once(
        agent_id,
        model_id,
        workspace_dir,
        OPENCLAW_AGENTS_ADD_FIRST_TIMEOUT_SECONDS,
        1,
    )
    if _verify_agent_registered(agent_id, workspace_dir, "after first create attempt"):
        if not first_ok:
            logger.warning(
                "Agent %s is registered after first create attempt despite non-zero/timeout result; continuing",
                agent_id,
            )
    else:
        logger.warning(
            "Agent %s not registered after first create attempt; retrying once with %.1fs timeout",
            agent_id,
            OPENCLAW_AGENTS_ADD_RETRY_TIMEOUT_SECONDS,
        )
        second_ok = _create_openclaw_agent_once(
            agent_id,
            model_id,
            workspace_dir,
            OPENCLAW_AGENTS_ADD_RETRY_TIMEOUT_SECONDS,
            2,
        )
        if not _verify_agent_registered(agent_id, workspace_dir, "after second create attempt"):
            message = (
                f"agent {agent_id} is still not registered with workspace {workspace_dir} "
                f"after two create attempts (first_ok={first_ok} second_ok={second_ok})"
            )
            logger.error(message)
            raise OpenClawAgentCreationError(message)
        if not second_ok:
            logger.warning(
                "Agent %s is registered after second create attempt despite non-zero/timeout result; continuing",
                agent_id,
            )

    # Configure models.json for the bench agent
    bench_agent_dir = _get_agent_store_dir(agent_id) / "agent"
    bench_agent_dir.mkdir(parents=True, exist_ok=True)
    bench_models = bench_agent_dir / "models.json"
    main_models = _openclaw_state_root() / "agents" / "main" / "agent" / "models.json"

    if base_url:
        # Custom OpenAI-compatible endpoint — build a provider entry
        data: dict[str, Any] = {}
        if main_models.exists():
            try:
                data = json.loads(main_models.read_text("utf-8-sig"))
            except (json.JSONDecodeError, OSError):
                data = {}

        key_ref = api_key if api_key else "${OPENAI_API_KEY}"
        providers = data.setdefault("models", {}).setdefault("providers", {})
        data["models"]["mode"] = "merge"
        providers["custom"] = {
            "baseUrl": base_url,
            "apiKey": key_ref,
            "api": "openai-completions",
            "models": [
                {
                    "id": model_id,
                    "name": model_id,
                    "reasoning": False,
                    "input": ["text"],
                    "contextWindow": 200000,
                    "maxTokens": 8192,
                }
            ],
        }
        data["defaultProvider"] = "custom"
        data["defaultModel"] = model_id
        bench_models.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        logger.info(
            "Configured custom provider (%s) with model %s for agent %s",
            base_url,
            model_id,
            agent_id,
        )
    elif main_models.exists():
        # Standard OpenRouter flow — copy main's models.json and set defaults
        import shutil as _shutil
        _shutil.copy2(main_models, bench_models)
        if "/" in model_id:
            provider_name, model_name = model_id.split("/", 1)
            try:
                raw = bench_models.read_text("utf-8-sig")
                data = json.loads(raw)
                data["defaultProvider"] = provider_name
                data["defaultModel"] = model_name
                bench_models.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), "utf-8"
                )
                logger.info(
                    "Set bench agent default model to %s / %s", provider_name, model_name
                )
            except Exception as exc:
                logger.warning("Failed to set default model in bench models.json: %s", exc)
        logger.info("Copied main agent models.json to bench agent %s", agent_id)

    # Delete sessions.json so OpenClaw picks up the new defaultProvider/defaultModel
    # instead of reusing a cached session entry that still points to an old model.
    bench_sessions_dir = _get_agent_store_dir(agent_id) / "sessions"
    sessions_store = bench_sessions_dir / "sessions.json"
    if sessions_store.exists():
        try:
            sessions_store.unlink()
            logger.info("Deleted stale sessions.json for bench agent %s", agent_id)
        except OSError as exc:
            logger.warning("Failed to delete sessions.json: %s", exc)

    # Copy bootstrap files from main workspace to bench agent workspace
    _BOOTSTRAP_FILES = [
        "SOUL.md", "BOOTSTRAP.md", "USER.md", "IDENTITY.md",
        "HEARTBEAT.md", "TOOLS.md", "AGENTS.md",
    ]
    main_workspace = _openclaw_workspace()
    if main_workspace.exists():
        for fname in _BOOTSTRAP_FILES:
            src = main_workspace / fname
            if src.exists():
                dest = workspace_dir / fname
                try:
                    import shutil as _shutil
                    _shutil.copy2(src, dest)
                    logger.info("Copied %s from main workspace to bench agent %s", fname, agent_id)
                except OSError as exc:
                    logger.warning("Failed to copy %s to bench workspace: %s", fname, exc)

    return True


def cleanup_agent_sessions(agent_id: str) -> None:
    """Remove stored session transcripts for an agent to avoid unbounded growth."""
    agent_dir = _get_agent_store_dir(agent_id)
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return
    removed = 0
    for pattern in ("*.jsonl", "*.jsonl.lock", "*.ndjson"):
        for path in sessions_dir.rglob(pattern):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove session file %s: %s", path, exc)
    sessions_store = sessions_dir / "sessions.json"
    if sessions_store.exists():
        try:
            sessions_store.unlink()
        except OSError as exc:
            logger.warning("Failed to remove session store %s: %s", sessions_store, exc)
    if removed:
        logger.info("Removed %s old OpenClaw session transcripts for %s", removed, agent_id)


def prepare_task_workspace(
    skill_dir: Path,
    run_id: str,
    task: Task,
    agent_id: str,
    *,
    copy_main_workspace_md: bool = False,
    link_skills: bool = False,
) -> Path:
    """
    Prepare workspace for a task by copying fixtures.
    Uses the agent's configured workspace to ensure files are in the right place.

    Args:
        skill_dir: Path to the skill directory containing assets.
        run_id: Unique run identifier.
        task: The Task object with workspace_files and metadata.
        agent_id: OpenClaw agent identifier.
        copy_main_workspace_md: If True, copy TOOLS.md and AGENTS.md from
            the main OpenClaw workspace (~/.openclaw/workspace/) into the
            benchmark agent's workspace. This ensures the subagent has access
            to the same MCP tool mapping and agent configuration as the main
            workspace. Defaults to False for backward compatibility.
        link_skills: If True, symlink the main OpenClaw skills directory into
            the benchmark workspace instead of copying each skill directory.
    """
    import shutil

    # Get agent's workspace from agent config
    workspace = _get_agent_workspace(agent_id)
    if workspace is None:
        # Fallback to task-specific workspace if agent workspace not found
        logger.warning("Could not find agent workspace, using fallback")
        workspace = Path(f"/tmp/pinchbench/{run_id}/{task.task_id}")

    _BOOTSTRAP_FILES = [
        "SOUL.md", "BOOTSTRAP.md", "USER.md", "IDENTITY.md", "HEARTBEAT.md", "TOOLS.md", "AGENTS.md",
    ]

    def _remove_readonly(func, path, _):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    saved_bootstrap: dict[str, bytes] = {}
    if workspace.exists():
        for fname in _BOOTSTRAP_FILES:
            fpath = workspace / fname
            if fpath.exists():
                saved_bootstrap[fname] = fpath.read_bytes()
        shutil.rmtree(workspace, onerror=_remove_readonly)
    workspace.mkdir(parents=True, exist_ok=True)

    for fname, content in saved_bootstrap.items():
        (workspace / fname).write_bytes(content)

    # When enabled, copy all bootstrap files from the main workspace so the
    # benchmark agent uses the same MCP tool mappings, agent configuration, etc.
    if copy_main_workspace_md:
        main_workspace = _openclaw_workspace()
        for fname in _BOOTSTRAP_FILES:
            src = main_workspace / fname
            if src.exists():
                dest = workspace / fname
                dest.write_bytes(src.read_bytes())
                logger.info(
                    "Copied %s from main workspace (%s) to benchmark workspace (%s)",
                    fname, src, dest,
                )
            else:
                logger.warning("Main workspace file %s not found at %s, skipping", fname, src)

    for file_spec in task.workspace_files:
        if "content" in file_spec:
            dest = workspace / file_spec["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(file_spec["content"])
            continue

        source = skill_dir / "assets" / file_spec["source"]
        dest = workspace / file_spec["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.write_bytes(source.read_bytes())
        except FileNotFoundError:
            logger.error("Workspace file not found: %s", source)
            raise

    # Provide skills from main workspace to benchmark workspace.
    # This enables benchmark agents to use installed skills like nano-pdf.
    main_skills_dir = _openclaw_workspace() / "skills"
    if main_skills_dir.exists():
        dest_skills_dir = workspace / "skills"
        if link_skills:
            try:
                if dest_skills_dir.is_symlink():
                    dest_skills_dir.unlink()
                elif dest_skills_dir.exists():
                    shutil.rmtree(dest_skills_dir, onerror=_remove_readonly)
                dest_skills_dir.symlink_to(main_skills_dir, target_is_directory=True)
                logger.info(
                    "Linked skills to benchmark workspace: %s -> %s",
                    dest_skills_dir,
                    main_skills_dir,
                )
                return workspace
            except OSError as exc:
                logger.warning(
                    "Failed to link skills, falling back to safe copy: %s -> %s, error=%s",
                    dest_skills_dir,
                    main_skills_dir,
                    exc,
                )

        dest_skills_dir.mkdir(parents=True, exist_ok=True)
        for skill_dir_src in main_skills_dir.iterdir():
            if skill_dir_src.is_dir():
                dest_skill_dir = dest_skills_dir / skill_dir_src.name
                # Copy skill directory
                import shutil

                if dest_skill_dir.exists():
                    shutil.rmtree(dest_skill_dir, onerror=_remove_readonly)
                ok = safe_copytree(skill_dir_src, dest_skill_dir)
                if not ok:
                    logger.warning(
                        "copy skill dir failed or partially skipped: %s -> %s",
                        skill_dir_src,
                        dest_skill_dir,
                    )
                logger.info("Copied skill to benchmark workspace: %s", skill_dir_src.name)

    return workspace


def _openclaw_state_root() -> Path:
    if os.environ.get("CLAWWEB_VERSION") != "openversion":
        return Path.home() / ".openclaw"
    # COSEC: the local dispatcher supplies the selected Bot profile; never fall back to another Bot.
    value = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if not value:
        raise RuntimeError("openversion requires OPENCLAW_STATE_DIR")
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("OpenClaw state root is not a directory")
    return root


def _openclaw_workspace() -> Path:
    if os.environ.get("CLAWWEB_VERSION") != "openversion":
        return Path.home() / ".openclaw" / "workspace"
    value = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if not value:
        raise RuntimeError("openversion requires OPENCLAW_WORKSPACE")
    workspace = Path(value).expanduser().resolve(strict=True)
    # COSEC: keep workspace reads in the already bound Bot profile, not the user's default profile.
    workspace.relative_to(_openclaw_state_root())
    return workspace


def _get_agent_store_dir(agent_id: str) -> Path:
    base_dir = _openclaw_state_root() / "agents"
    if os.environ.get("CLAWWEB_VERSION") == "openversion":
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", agent_id) or ".." in agent_id:
            raise RuntimeError("Invalid local agent ID")
        for candidate in (base_dir / agent_id, base_dir / agent_id.replace(":", "-").lower()):
            candidate.resolve().relative_to(_openclaw_state_root())
    # OpenClaw normalizes agent IDs to lowercase and replaces colons with dashes
    normalized_id = agent_id.replace(":", "-").lower()
    direct_dir = base_dir / agent_id
    if direct_dir.exists():
        return direct_dir
    normalized_dir = base_dir / normalized_id
    if normalized_dir.exists():
        return normalized_dir
    return direct_dir


def _resolve_session_id_from_store(agent_id: str, session_id: str | None = None) -> str | None:
    agent_dir = _get_agent_store_dir(agent_id)
    sessions_store = agent_dir / "sessions" / "sessions.json"
    if not sessions_store.exists():
        return None
    try:
        sessions_payload = json.loads(sessions_store.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse sessions store: %s", exc)
        return None
    if not isinstance(sessions_payload, dict):
        return None

    normalized_id = agent_id.replace(":", "-").lower()
    if session_id:
        explicit_keys = [
            f"agent:{agent_id}:explicit:{session_id}",
            f"agent:{normalized_id}:explicit:{session_id}",
        ]
        for key in explicit_keys:
            entry = sessions_payload.get(key)
            if isinstance(entry, dict) and entry.get("sessionId"):
                return entry["sessionId"]

        for entry in sessions_payload.values():
            if isinstance(entry, dict) and entry.get("sessionId") == session_id:
                return session_id

    preferred_keys = [
        f"agent:{agent_id}:main",
        f"agent:{agent_id}:default",
        f"agent:{normalized_id}:main",
        f"agent:{normalized_id}:default",
    ]
    for key in preferred_keys:
        entry = sessions_payload.get(key)
        if isinstance(entry, dict) and entry.get("sessionId"):
            return entry["sessionId"]

    newest_entry = None
    newest_timestamp = -1
    for entry in sessions_payload.values():
        if not isinstance(entry, dict):
            continue
        if "sessionId" not in entry:
            continue
        updated_at = entry.get("updatedAt")
        if isinstance(updated_at, (int, float)) and updated_at > newest_timestamp:
            newest_timestamp = updated_at
            newest_entry = entry
    if newest_entry:
        return newest_entry.get("sessionId")
    return None


def _find_transcript_path_from_sessions_store(
    agent_id: str, session_id: str | None = None
) -> Optional[Path]:
    """Best-effort transcript path resolution from sessions.json payload values."""
    agent_dir = _get_agent_store_dir(agent_id)
    sessions_store = agent_dir / "sessions" / "sessions.json"
    if not sessions_store.exists():
        return None
    try:
        payload = json.loads(sessions_store.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    def _iter_strings(node: Any):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from _iter_strings(value)
        elif isinstance(node, list):
            for value in node:
                yield from _iter_strings(value)

    suffixes = (".jsonl", ".ndjson")
    session_root = agent_dir / "sessions"

    values_to_scan: list[Any]
    if session_id:
        normalized_id = agent_id.replace(":", "-").lower()
        explicit_keys = [
            f"agent:{agent_id}:explicit:{session_id}",
            f"agent:{normalized_id}:explicit:{session_id}",
        ]
        values_to_scan = [
            value
            for key, value in payload.items()
            if key in explicit_keys
            or (isinstance(value, dict) and value.get("sessionId") == session_id)
        ]
    else:
        values_to_scan = [payload]

    for value in _iter_strings(values_to_scan):
        if not value.endswith(suffixes):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = session_root / value
        if candidate.exists():
            return candidate
    return None


def _find_recent_session_path(agent_dir: Path, started_at: float) -> Path | None:
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return None
    candidates = list(sessions_dir.rglob("*.jsonl")) + list(sessions_dir.rglob("*.ndjson"))
    if not candidates:
        return None
    tolerance_seconds = 5.0
    recent_candidates = [
        path for path in candidates if path.stat().st_mtime >= (started_at - tolerance_seconds)
    ]
    pool = recent_candidates or candidates
    return max(pool, key=lambda path: path.stat().st_mtime)


def _load_transcript(
    agent_id: str, session_id: str, started_at: float
) -> tuple[List[Dict[str, Any]], Optional[Path]]:
    agent_dir = _get_agent_store_dir(agent_id)
    transcript_path = None

    # Prefer the exact --session-id used for this benchmark run.  A bench agent
    # can also have a "main" session in sessions.json; reading that instead of
    # the explicit task session gives empty or unrelated transcripts.
    #
    # Strategy:
    #   1. Retry the passed session_id and matching explicit sessions.json entry
    #   2. Fallback to legacy sessions.json main/default/newest resolution
    #   3. Glob for any .jsonl in the sessions dir (most-recently-modified)
    for attempt in range(TRANSCRIPT_EXACT_LOOKUP_ATTEMPTS):
        # 1. Try our passed-in session ID first.
        for direct_path in (
            agent_dir / "sessions" / f"{session_id}.jsonl",
            agent_dir / "sessions" / f"{session_id}.ndjson",
        ):
            if direct_path.exists():
                transcript_path = direct_path
                logger.info(
                    "Found transcript via passed session ID: %s (attempt %s)",
                    direct_path.name,
                    attempt + 1,
                )
                break
        if transcript_path is not None:
            break

        # 2. Try the explicit sessions.json entry for this session ID.
        resolved_session_id = _resolve_session_id_from_store(agent_id, session_id)
        if resolved_session_id:
            session_dir = agent_dir / "sessions"
            for candidate in (
                session_dir / f"{resolved_session_id}.jsonl",
                session_dir / f"{resolved_session_id}.ndjson",
                session_dir / resolved_session_id / "transcript.jsonl",
                session_dir / resolved_session_id / "events.jsonl",
            ):
                if candidate.exists():
                    transcript_path = candidate
                    logger.info(
                        "Found transcript via sessions.json: %s (attempt %s)",
                        candidate.name,
                        attempt + 1,
                    )
                    break
            if transcript_path is not None:
                break

        # 2b. Parse transcript-like paths from the matching sessions.json entry.
        candidate_from_store = _find_transcript_path_from_sessions_store(agent_id, session_id)
        if candidate_from_store is not None:
            transcript_path = candidate_from_store
            logger.info(
                "Found transcript via sessions.json path: %s (attempt %s)",
                candidate_from_store,
                attempt + 1,
            )
            break

        if attempt < TRANSCRIPT_EXACT_LOOKUP_ATTEMPTS - 1:
            time.sleep(1.0)

    if transcript_path is None:
        # Legacy fallback: older OpenClaw builds may ignore --session-id and
        # store only a UUID/main/default mapping.  Use this only after exact
        # matching fails, so explicit benchmark sessions win when present.
        logger.warning(
            "Exact transcript for agent %s session %s not found after %ss; "
            "falling back to legacy session resolution",
            agent_id,
            session_id,
            TRANSCRIPT_EXACT_LOOKUP_ATTEMPTS,
        )
        resolved_session_id = _resolve_session_id_from_store(agent_id)
        if resolved_session_id:
            session_dir = agent_dir / "sessions"
            for candidate in (
                session_dir / f"{resolved_session_id}.jsonl",
                session_dir / f"{resolved_session_id}.ndjson",
                session_dir / resolved_session_id / "transcript.jsonl",
                session_dir / resolved_session_id / "events.jsonl",
            ):
                if candidate.exists():
                    transcript_path = candidate
                    logger.info("Found transcript via legacy sessions.json: %s", candidate.name)
                    break

    if transcript_path is None:
        candidate_from_store = _find_transcript_path_from_sessions_store(agent_id)
        if candidate_from_store is not None:
            transcript_path = candidate_from_store
            logger.info("Found transcript via legacy sessions.json path: %s", candidate_from_store)

    if transcript_path is None:
        recent_path = _find_recent_session_path(agent_dir, started_at)
        if recent_path is not None:
            transcript_path = recent_path
            logger.info("Found transcript via glob fallback: %s", recent_path.name)

    if transcript_path is None:
        sessions_dir = agent_dir / "sessions"
        if sessions_dir.exists():
            all_files = list(sessions_dir.iterdir())
            logger.warning(
                "Transcript not found for agent %s. Sessions dir contents: %s",
                agent_id,
                [f.name for f in all_files],
            )
            sessions_store = sessions_dir / "sessions.json"
            if sessions_store.exists():
                try:
                    payload_preview = sessions_store.read_text(encoding="utf-8")[:1200]
                    logger.warning("sessions.json preview: %s", payload_preview)
                except OSError as exc:
                    logger.warning("Could not read sessions.json preview: %s", exc)
        else:
            logger.warning(
                "Transcript not found — sessions dir does not exist: %s",
                sessions_dir,
            )
        return [], None

    transcript: List[Dict[str, Any]] = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            transcript.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse transcript line: %s", exc)
            transcript.append({"raw": line, "parse_error": str(exc)})
    return transcript, transcript_path


def _resolve_engine_db(oc_home: str) -> str | None:
    """Find the workflow flow DB under *oc_home*.

    The current engine stores runs in ``<oc_home>/flows/registry.sqlite``
    (schema: ``flow_runs(flow_id, status, goal, state_json, created_at, ended_at, ...)``).
    Older engines used ``engine.db`` (schema had ``workflow_id`` + ``started_at``).
    Both are accepted; ``registry.sqlite`` is tried first because it is the live
    primary store. Callers branch SQL on the actual schema (see
    ``_flow_run_schema`` / ``_find_flow_id_by_workflow``), so this no longer
    assumes a particular column layout.

    Waits up to 10 s for the file to appear (the engine may lazy-create it).
    Returns the resolved path, or ``None`` if none of the candidates exist.
    """
    import os as _os
    import time as _time

    candidates = [
        _os.path.join(oc_home, "flows", "registry.sqlite"),       # current primary store
        _os.path.join(oc_home, "workflow", "engine.db"),           # legacy
        _os.path.join(oc_home, "workflow-engine", "engine.db"),    # legacy
        _os.path.join(oc_home, "logs", "clawmind", "engine.db"),   # legacy
        _os.path.join(oc_home, "logs", "workflow-engine", "engine.db"),  # legacy
    ]
    # Deduplicate while preserving order
    seen = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    deadline = _time.time() + 10.0
    while True:
        for c in unique:
            if _os.path.isfile(c):
                return c
        if _time.time() >= deadline:
            break
        _time.sleep(1.0)

    return None


def _flow_run_schema(db_path: str) -> set | None:
    """Column names of ``flow_runs`` in *db_path*, or ``None`` if absent/unreadable.

    Two historical shapes:
    - ``engine.db``: columns include ``workflow_id`` + ``started_at``.
    - ``registry.sqlite`` (current primary store): no ``workflow_id``/``started_at``;
      workflow identity lives inside ``state_json``; timing is ``created_at``/``ended_at``.

    Used to branch the flow_id lookup SQL so Path A works against either schema
    without guessing columns.
    """
    import sqlite3 as _sqlite3
    try:
        conn = _sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(flow_runs)").fetchall()}
        conn.close()
        return cols if cols else None
    except Exception as exc:
        logger.warning("Could not read flow_runs schema from %s: %s", db_path, exc)
        return None


def _find_flow_id_by_workflow(db_path: str, workflow_id: str, start_ms: int) -> str | None:
    """Resolve a ``flow_id`` for *workflow_id* created at/after *start_ms* (ms epoch).

    Schema-aware:
    - ``engine.db`` shape (has ``workflow_id``): ``WHERE workflow_id=? AND <time>=?``.
    - ``registry.sqlite`` shape: ``state_json`` (a JSON document, possibly stored
      **double-escaped** with ``\\"`` instead of plain ``"``) carries the workflow
      snapshot. Match the bare ``<workflow_id>`` substring — NOT a quoted boundary
      ``'"<id>"'``: the real column stores escaped quotes (``\\"<id>\\"``), so a
      quote-delimited LIKE pattern never matches → returns ``None`` → empty
      transcript → 0. The bare token is collision-safe because the configured
      ``workflow_id`` is the hyphenated long form (e.g. ``workflow-test-agent-modes``);
      a truncated/other workflow's row does not contain that exact compound token,
      and ``created_at >= start_ms`` further scopes to the current bench window.

    Best-effort, never raises (any error → ``None``). A ``None`` return does NOT mean
    the workflow didn't run — registry's ``state_json`` may simply lack a matchable
    workflow_id token (older row / shape drift). Callers MUST fall back to filesystem
    directory discovery (``_find_flow_id_fs``), which is the canonical
    ``workflow_id``→``flow_id`` mapping via ``<root>/<workflow_id>/<flow_id>/``.
    """
    import sqlite3 as _sqlite3
    cols = _flow_run_schema(db_path)
    if not cols:
        return None
    # Workflow ids are controlled (from YAML/ClawWeb, hyphenated [a-z0-9-]), but guard
    # against stray LIKE wildcards so a literal %/_ in the id can't widen the match.
    _esc = workflow_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pat = f"%{_esc}%"
    try:
        conn = _sqlite3.connect(db_path)
        if "workflow_id" in cols:
            time_col = "started_at" if "started_at" in cols else "created_at"
            row = conn.execute(
                f"SELECT flow_id FROM flow_runs WHERE workflow_id = ? "
                f"AND {time_col} >= ? ORDER BY {time_col} ASC LIMIT 1",
                (workflow_id, start_ms),
            ).fetchone()
        else:
            # registry.sqlite: bare-substring reverse-lookup (escapes-agnostic).
            # Quoted-boundary LIKE '%"id"%' misses because the column is double-escaped.
            row = conn.execute(
                "SELECT flow_id FROM flow_runs WHERE state_json LIKE ? ESCAPE '\\' "
                "AND created_at >= ? ORDER BY created_at ASC LIMIT 1",
                (like_pat, start_ms),
            ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("flow_id lookup failed in %s for workflow %s: %s", db_path, workflow_id, exc)
        return None


def _poll_flow_status(db_path: str, flow_id: str) -> str | None:
    """Poll ``flow_runs.status`` for *flow_id*. Both DB shapes have status + flow_id.

    Returns the status string, or ``None`` if unreadable. Best-effort, never raises.
    """
    import sqlite3 as _sqlite3
    try:
        conn = _sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status FROM flow_runs WHERE flow_id = ?", (flow_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("status poll failed in %s for flow %s: %s", db_path, flow_id, exc)
        return None


def _flow_matches_session(flow_dir: str, fingerprint: str, session_id: str) -> bool:
    """Check if a flow directory belongs to the current bench session.

    Strategy (in order):
    1. Read the first node's JSONL (route-detect or equivalent) and check if
       the goal/raw_message contains the fingerprint.
    2. Check the trajectory-path.json session_id against the bench session_id.
    """
    import os as _os
    import json as _json

    if not fingerprint and not session_id:
        return True  # No filter → accept all

    # ── Strategy 1: Check first synthetic node output for matching goal ──
    # Look for any *-attempt-*.jsonl file (non-trajectory) from the first node
    jsonl_files = sorted(
        f for f in _os.listdir(flow_dir)
        if f.endswith('.jsonl') and 'trajectory' not in f
    )
    for jf in jsonl_files:
        jpath = _os.path.join(flow_dir, jf)
        try:
            with open(jpath, 'r') as fh:
                for line in fh:
                    try:
                        entry = _json.loads(line.strip())
                    except (_json.JSONDecodeError, ValueError):
                        continue
                    # Check if raw_message/goal in node output contains fingerprint
                    output = entry.get('__node_output__', {})
                    if isinstance(output, dict):
                        text = str(output.get('text', ''))
                        if fingerprint and fingerprint in text:
                            return True
                    # Also check data field for route-detect-style nodes
                    data = entry.get('data', {})
                    if isinstance(data, dict):
                        text = str(data.get('text', ''))
                        if fingerprint and fingerprint in text:
                            return True
        except OSError:
            continue

    # ── Strategy 2: Check trajectory-path.json for session_id match ──
    for f in _os.listdir(flow_dir):
        if f.endswith('trajectory-path.json'):
            tpath = _os.path.join(flow_dir, f)
            try:
                with open(tpath, 'r') as fh:
                    tp = _json.load(fh)
                    tp_session = tp.get('sessionId', '')
                    # bench session_id format: "task_xxx_<timestamp>"
                    # trajectory sessionId format: UUID
                    # So we check if this flow was created by a session that
                    # contains traces of our expected_prompt
                    if session_id and tp_session:
                        pass  # UUID vs task_id doesn't directly match
            except (OSError, _json.JSONDecodeError, ValueError):
                continue

    return False


def _find_flow_id_fs(
    session_root: str,
    workflow_id: str,
    started_at: float,
    existing_flow_dirs: set,
    session_id: str = "",
    expected_prompt: str = "",
) -> str | None:
    """Scan ``<session_root>/<workflow_id>/`` for a new flow directory.

    Returns the first directory whose mtime >= *started_at* AND whose content
    matches the bench session (by goal text or session key), to avoid matching
    flows from concurrent main-agent sessions. When *existing_flow_dirs* is
    non-empty, only flows NOT in that set are considered (Path B snapshot diff).
    """
    import os as _os
    import json as _json
    import sqlite3

    wf_dir = _os.path.join(session_root, workflow_id)
    if not _os.path.isdir(wf_dir):
        return None

    # Build a short fingerprint from the expected prompt for fuzzy matching.
    # Extract the first meaningful identifier: task_id, target account, or event ID.
    fingerprint = ""
    if expected_prompt:
        # Try to extract task_id or target as a quick fingerprint
        import re
        m = re.search(r'"task_id"\s*:\s*"([^"]+)"', expected_prompt)
        if not m:
            m = re.search(r'"target"\s*:\s*"([^"]+)"', expected_prompt)
        if not m:
            m = re.search(r'行动态注入', expected_prompt)  # fallback — unlikely
        if m:
            fingerprint = m.group(1)

    candidates = []  # (mtime, name) for flows that pass mtime filter
    for name in sorted(_os.listdir(wf_dir)):
        dpath = _os.path.join(wf_dir, name)
        if not _os.path.isdir(dpath):
            continue
        # Path B snapshot diff: skip pre-existing dirs
        if existing_flow_dirs and name in existing_flow_dirs:
            continue
        try:
            mtime = _os.path.getmtime(dpath)
            if mtime >= started_at:
                candidates.append((mtime, name, dpath))
        except OSError:
            continue

    if not candidates:
        return None

    # ── Precision filter: verify flow content matches this bench session ──
    if fingerprint or session_id:
        valid = []
        for mtime, name, dpath in candidates:
            if _flow_matches_session(dpath, fingerprint, session_id):
                valid.append((mtime, name))
        if valid:
            # Prefer the match; if multiple, take the one with latest mtime
            valid.sort(key=lambda x: x[0], reverse=True)
            return valid[0][1]
        # If fingerprint filtering produced no matches, fall back to first mtime match
        # but log a warning so users can see the ambiguity
        logger.warning(
            "_find_flow_id_fs: %d candidate(s) passed mtime filter, but none matched "
            "fingerprint=%s or session_id=%s; falling back to first mtime match (may be wrong flow)",
            len(candidates), fingerprint[:80] if fingerprint else "(none)", session_id[:80],
        )

    # No fingerprint or fingerprint matched nothing → first mtime match
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _wait_for_workflow_finished(
    oc_home: str, flow_id: str, deadline: float, poll_interval: float
) -> bool:
    """Poll ClawMind JSONL log for ``workflow_finished`` with matching *flow_id*.

    This is the most reliable way to detect workflow completion because
    ClawMind emits a structured event with the final status.  The JSONL log is
    rotated daily (``clawmind-YYYY-MM-DD.jsonl``).
    """
    import os as _os
    import time as _time
    import json as _json

    log_dir = _os.path.join(oc_home, "logs", "clawmind")
    today = _time.strftime("%Y-%m-%d")
    log_path = _os.path.join(log_dir, f"clawmind-{today}.jsonl")

    # Also check yesterday's log for midnight-crossing runs
    candidates = [log_path]
    yesterday = _time.strftime(
        "%Y-%m-%d", _time.localtime(_time.time() - 86400)
    )
    candidates.append(_os.path.join(log_dir, f"clawmind-{yesterday}.jsonl"))

    while _time.time() < deadline:
        for cand in candidates:
            if not _os.path.isfile(cand):
                continue
            try:
                with open(cand, "r") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = _json.loads(line)
                        except _json.JSONDecodeError:
                            continue
                        if evt.get("event_type") == "workflow_finished":
                            if evt.get("flow_id") == flow_id:
                                return True
            except Exception:
                pass
        _time.sleep(poll_interval)
    return False


def _extract_workflow_flow_id(
    agent_id: str,
    session_id: str,
    started_at: float,
    workflow_id: str,
) -> str | None:
    """Extract flow_id from the bench agent's session transcript.

    When the bench agent receives a workflow task, it calls
    ``workflow_engine_dispatch`` (per AGENTS.md routing). The tool result
    contains the flow_id. This is the most reliable way to identify the
    correct flow — much better than DB/FS discovery which breaks when
    multiple flows for the same workflow run concurrently.

    Returns the flow_id, or None if not found.
    """
    import os as _os
    import json as _json
    from session_io import load_jsonl as _si_load_jsonl

    agent_dir = _get_agent_sessions_dir(agent_id)
    if not agent_dir or not _os.path.isdir(agent_dir):
        return None

    # Find the most recent session file created after started_at
    candidates = []
    for fname in _os.listdir(agent_dir):
        fpath = _os.path.join(agent_dir, fname)
        if not fname.endswith('.jsonl'):
            continue
        if 'trajectory' in fname:
            continue
        try:
            mtime = _os.path.getmtime(fpath)
            if mtime >= started_at:
                candidates.append((mtime, fpath))
        except OSError:
            continue
    if not candidates:
        return None

    # Search newest first
    candidates.sort(key=lambda x: x[0], reverse=True)
    for _, fpath in candidates:
        entries = _si_load_jsonl(fpath)
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            if msg.get("role") == "toolResult":
                content = msg.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                for c in content:
                    if c.get("type") != "text":
                        continue
                    text = c.get("text", "")
                    # Look for flow_id in the tool result
                    # Format: {"status": "started", "workflowId": "...", "flowId": "...", ...}
                    if '"flowId"' in text or '"flow_id"' in text:
                        try:
                            result = _json.loads(text) if isinstance(text, str) else text
                            fid = result.get("flowId") or result.get("flow_id")
                            if fid and isinstance(fid, str):
                                logger.info(
                                    "Extracted flow_id=%s from dispatch result (session=%s)",
                                    fid, fname,
                                )
                                return fid
                        except (_json.JSONDecodeError, ValueError, TypeError):
                            # The tool result might have flow_id in the text but not as top-level JSON
                            import re
                            m = re.search(r'"flowId"\s*:\s*"([^"]+)"', text)
                            if m:
                                fid = m.group(1)
                                logger.info(
                                    "Extracted flow_id=%s via regex from dispatch result (session=%s)",
                                    fid, fname,
                                )
                                return fid
    return None


def _get_agent_sessions_dir(agent_id: str) -> str | None:
    """Get the sessions directory for a bench agent."""
    import os as _os
    from session_io import resolve_openclaw_home
    oc_home = resolve_openclaw_home()
    sessions_dir = _os.path.join(oc_home, "agents", agent_id, "sessions")
    return sessions_dir if _os.path.isdir(sessions_dir) else None


def _load_workflow_transcript(
    agent_id: str,
    session_id: str,
    started_at: float,
    task: Any,
    explicit_flow_id: str | None = None,
) -> tuple:
    """Load and merge workflow multi-node transcript from embedded sessions.

    Workflow runs produce per-node JSONL files under
    ``<openclaw_home>/logs/clawmind/embedded-sessions/<workflowId>/<flowId>/``.

    Strategy (two paths):

    **Path A — workflow flow DB available (registry.sqlite OR engine.db)**
    1. Resolve the flow DB (``flows/registry.sqlite`` preferred, ``engine.db`` legacy)
    2. Look up the flow_id for this run (schema-aware: ``workflow_id`` column for
       engine.db, ``state_json`` LIKE for registry.sqlite; filesystem directory
       discovery as fallback if the lookup misses)
    3. Poll the DB ``flow_runs.status`` until the workflow finishes (JSONL log as
       secondary if status is unreadable)
    4. Locate the session directory via ``session_io.find_session_dir()`` and merge

    **Path B — no flow DB (filesystem fallback)**
    1. Snapshot existing flow directories under ``<root>/<workflow_id>/``
    2. Poll for a new flow directory to appear (mtime >= started_at)
    3. Wait for ``*-attempt-*.jsonl`` files to be written
    4. Merge → return transcript

    Returns:
        (transcript: list[dict], merged_path: Path|None)
    """
    import os as _os
    import time as _time
    import tempfile as _tempfile

    from session_io import (
        resolve_openclaw_home,
        resolve_session_root,
        find_session_dir,
        load_jsonl as _si_load_jsonl,
    )
    from workflow_merge import merge_session as _merge_workflow_session

    workflow_id = task.frontmatter.get("workflow_id", "") if hasattr(task, "frontmatter") else ""
    if not workflow_id:
        logger.error("Workflow task missing workflow_id in frontmatter")
        return [], None

    oc_home = resolve_openclaw_home()
    poll_interval = 2.0
    max_wait = min(getattr(task, "timeout_seconds", 120) * 1.0, 180.0)

    # ── Resolve engine.db (with retries) ──
    engine_db = _resolve_engine_db(oc_home)

    flow_id = ""
    # ── Explicit flow_id from dispatch tool result (most reliable) ──
    if explicit_flow_id:
        flow_id = explicit_flow_id
        logger.info(
            "Using explicit flow_id=%s from dispatch result (session=%s)",
            flow_id, session_id,
        )

    if not flow_id and engine_db is not None:
        # ================================================================
        # Path A: workflow flow DB available (registry.sqlite OR engine.db).
        # SQL is schema-aware; registry.sqlite is the current primary store.
        # ================================================================
        schema = _flow_run_schema(engine_db)
        db_kind = "registry.sqlite" if schema and "workflow_id" not in schema else "engine.db"
        logger.info("Using workflow flow DB at %s (kind=%s)", engine_db, db_kind)

        # ── A1. Resolve flow_id (schema-aware), filesystem fallback if miss ──
        start_ms = int(started_at * 1000)
        flow_id = _find_flow_id_by_workflow(engine_db, workflow_id, start_ms)

        if not flow_id:
            # registry reverse-lookup can miss when state_json lacks a matchable
            # workflow_id token. The directory layout <root>/<workflow_id>/<flow_id>/
            # is the canonical mapping — use it rather than bailing to an empty
            # transcript (which would silently score 0).
            logger.warning(
                "flow_id reverse-lookup miss for workflow=%s in %s; "
                "falling back to filesystem directory discovery",
                workflow_id, engine_db,
            )
            session_root_a = resolve_session_root(oc_home)
            flow_id = _find_flow_id_fs(
                session_root_a, workflow_id, started_at, set(),
                session_id=session_id,
                expected_prompt=getattr(task, 'prompt', ''),
            )

        if not flow_id:
            logger.error(
                "No flow_id found for workflow %s after startup time %.0f (session=%s)",
                workflow_id, started_at, session_id,
            )
            return [], None

        logger.info("Found flow_id=%s for workflow=%s (session=%s)", flow_id, workflow_id, session_id)

    # ── Wait for completion (DB poll if available, else ClawMind JSONL) ──
    if engine_db is not None and flow_id:
        elapsed = 0.0
        final_status = None
        terminal = ("succeeded", "failed", "blocked", "skipped")
        while elapsed < max_wait:
            final_status = _poll_flow_status(engine_db, flow_id)
            if final_status in terminal:
                break
            _time.sleep(poll_interval)
            elapsed += poll_interval

        if final_status is None:
            # DB status unreadable the whole time — fall back to the ClawMind JSONL
            # completion signal for whatever wait budget remains.
            logger.warning(
                "flow status unreadable from %s for %s after %.1fs; trying ClawMind JSONL log",
                engine_db, flow_id, elapsed,
            )
            deadline = _time.time() + max(0.0, max_wait - elapsed)
            _wait_for_workflow_finished(oc_home, flow_id, deadline, poll_interval)
        elif final_status == "blocked":
            logger.info("Workflow %s flow %s: blocked after timeout → treating as special success", workflow_id, flow_id)
        elif final_status != "succeeded":
            logger.warning("Workflow %s flow %s: final status=%s", workflow_id, flow_id, final_status)
        else:
            logger.info("Workflow %s flow %s: completed (status=%s) in %.1fs", workflow_id, flow_id, final_status, elapsed)
    elif flow_id:
        # No engine.db — wait via ClawMind JSONL log
        logger.info(
            "engine.db unavailable — waiting for workflow %s flow %s via ClawMind log",
            workflow_id, flow_id,
        )
        deadline = _time.time() + max_wait
        _wait_for_workflow_finished(oc_home, flow_id, deadline, poll_interval)

    # Path B: engine.db unavailable + no explicit flow_id — filesystem discovery
    if not flow_id:
        logger.info("engine.db not found — using filesystem fallback for workflow %s", workflow_id)

        session_root = resolve_session_root(oc_home)
        wf_dir = _os.path.join(session_root, workflow_id)

        # B1. Snapshot existing flow directories (baseline)
        existing_flow_dirs: set = set()
        if _os.path.isdir(wf_dir):
            for name in _os.listdir(wf_dir):
                if _os.path.isdir(_os.path.join(wf_dir, name)):
                    existing_flow_dirs.add(name)
        logger.info(
            "FS fallback: baseline snapshot of %s → %d existing flow dirs",
            wf_dir, len(existing_flow_dirs),
        )

        # B2. Poll for new flow directory
        elapsed = 0.0
        flow_id = None
        while elapsed < max_wait:
            flow_id = _find_flow_id_fs(
                session_root, workflow_id, started_at, existing_flow_dirs,
                session_id=session_id,
                expected_prompt=getattr(task, 'prompt', ''),
            )
            if flow_id:
                break
            _time.sleep(poll_interval)
            elapsed += poll_interval

        if not flow_id:
            logger.error(
                "FS fallback: no new flow directory found for workflow=%s after %.1fs",
                workflow_id, elapsed,
            )
            return [], None

        logger.info("FS fallback: found flow_id=%s for workflow=%s after %.1fs", flow_id, workflow_id, elapsed)

        # B3. Wait for workflow to complete via ClawMind log
        session_dir = _os.path.join(session_root, workflow_id, flow_id)
        deadline = _time.time() + max_wait
        finished = _wait_for_workflow_finished(oc_home, flow_id, deadline, poll_interval)
        if finished:
            logger.info(
                "FS fallback: workflow %s flow %s completed → waiting %ds for file writes",
                workflow_id, flow_id, int(poll_interval * 2),
            )
            # Give the engine a moment to flush session files to disk
            _time.sleep(poll_interval * 2)
        else:
            logger.warning(
                "FS fallback: workflow %s flow %s did not finish within %.1fs — "
                "attempting merge of whatever files exist",
                workflow_id, flow_id, max_wait,
            )
        # Check session directory exists
        actual_dir = find_session_dir(flow_id, workflow_id, session_root=session_root)
        if actual_dir:
            session_dir = actual_dir
        if not _os.path.isdir(session_dir):
            logger.error("FS fallback: session files not found in %s after %.1fs", session_dir, max_wait)
            return [], None

    # ── 3. Locate session directory (engine.db path A uses flow_id from SQL) ──
    if engine_db is not None:
        session_dir = find_session_dir(flow_id, workflow_id)
    if not session_dir:
        logger.error("Session directory not found for workflow=%s flow=%s", workflow_id, flow_id)
        return [], None

    # ── 4. Build workflow trace (multi-source; best-effort) ──
    workflow_trace = None
    subagent_sessions = {}
    try:
        from workflow_flow import build_workflow_trace as _build_wf_trace
        workflow_trace = _build_wf_trace(flow_id, workflow_id, oc_home, session_dir)
        subagent_sessions = workflow_trace.get("subagent_sessions", {})
        logger.info(
            "Workflow trace built: status=%s, nodes=%d, dag=%d, timeline=%d events, subagent_sessions=%d",
            workflow_trace.get("status", "?"),
            len(workflow_trace.get("node_executions", [])),
            len(workflow_trace.get("dag", [])),
            len(workflow_trace.get("timeline", [])),
            len(subagent_sessions),
        )
    except Exception as exc:
        logger.warning("build_workflow_trace failed (grading continues without flow trace): %s", exc)

    # ── 5. Merge node files ──
    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as _tmp:
        merged_path_str = _tmp.name

    try:
        import pathlib as _pl
        # Extract DAG and node order from workflow_trace for proper topological sorting
        _trace_dag = None
        _trace_yaml_order = None
        if isinstance(workflow_trace, dict):
            _dag_entries = workflow_trace.get("dag", [])
            if _dag_entries:
                _trace_dag = {}
                _trace_yaml_order = []
                for _d_entry in _dag_entries:
                    _node_id = _d_entry.get("node", "")
                    if _node_id:
                        _trace_dag[_node_id] = _d_entry.get("deps", [])
                        _trace_yaml_order.append(_node_id)

        stats = _merge_workflow_session(
            session_dir=_pl.Path(session_dir),
            output_path=_pl.Path(merged_path_str),
            pattern="{node}-attempt-1.jsonl",
            dag=_trace_dag or None,
            yaml_order=_trace_yaml_order or None,
            workflow_trace=workflow_trace,
            external_sessions=subagent_sessions or None,
        )
    except Exception as exc:
        logger.error("Merge failed for %s: %s", flow_id, exc)
        return [], None

    if not stats or stats.get("total_events", 0) == 0:
        logger.error("Merge produced empty transcript for %s (nodes: %s)", flow_id, stats.get("nodes", []) if stats else "?")
        return [], None

    logger.info(
        "Merged workflow transcript: %d nodes, %d events → %s",
        stats.get("node_count", len(stats.get("nodes", []))),
        stats.get("total_events", 0),
        merged_path_str,
    )

    # ── 6. Read merged transcript ──
    transcript = _si_load_jsonl(merged_path_str)
    return transcript, Path(merged_path_str)


def _extract_usage_from_transcript(transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum token usage and cost from real assistant model calls in transcript."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
    }

    for entry in transcript:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue
        if msg.get("api") == "cli":
            continue
        totals["request_count"] += 1
        usage = msg.get("usage", {})
        totals["input_tokens"] += usage.get("input", 0)
        totals["output_tokens"] += usage.get("output", 0)
        totals["cache_read_tokens"] += usage.get("cacheRead", 0)
        totals["cache_write_tokens"] += usage.get("cacheWrite", 0)
        totals["total_tokens"] += usage.get("totalTokens", 0)
        cost = usage.get("cost", {})
        totals["cost_usd"] += cost.get("total", 0.0)

    return totals


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from OpenClaw CLI output."""
    if not text:
        return ""
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def _preview_text(text: str, max_chars: int = INTERACTION_PREVIEW_CHARS) -> str:
    text = (text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... <truncated {len(text) - max_chars} chars>"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    chunks.append(str(item.get("text", "")))
                elif "text" in item:
                    chunks.append(str(item.get("text", "")))
            elif isinstance(item, str):
                chunks.append(item)
        return "\n".join(chunk for chunk in chunks if chunk)
    if content is None:
        return ""
    return str(content)


def _collect_messages_text(events: List[Dict[str, Any]], role: str) -> str:
    chunks: list[str] = []
    for entry in events:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != role:
            continue
        text = _content_to_text(msg.get("content", ""))
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _normalize_interactions(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    raw = frontmatter.get("interactions")
    if not raw:
        return {}
    if isinstance(raw, list):
        rules = [entry for entry in raw if isinstance(entry, dict)]
        return {"max_turns": 10, "rules": rules}
    if not isinstance(raw, dict):
        logger.warning("Ignoring invalid interactions config: %s", raw)
        return {}

    max_turns = raw.get("max_turns", 10)
    try:
        max_turns = max(0, int(max_turns))
    except (TypeError, ValueError):
        max_turns = 10

    rules = raw.get("rules", [])
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        rules = []
    rules = [rule for rule in rules if isinstance(rule, dict)]

    judge_policy = raw.get("judge_policy")
    if not isinstance(judge_policy, dict):
        judge_policy = None

    return {"max_turns": max_turns, "rules": rules, "judge_policy": judge_policy}


def _match_interaction_reply(text: str, rules: List[Dict[str, Any]]) -> tuple[str, Dict[str, Any] | None]:
    haystack = text or ""
    for rule in rules:
        needle = str(rule.get("on_output_contains") or "")
        reply = rule.get("reply")
        if needle and needle in haystack and reply is not None:
            return str(reply), rule
    return "", None


def _run_openclaw_message(
    *,
    agent_id: str,
    session_id: str,
    message: str,
    workspace: Path,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return run_openclaw_agent(
        agent_id=agent_id,
        session_id=session_id,
        message=message,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        runner=subprocess.run,
        use_shell=USE_SHELL,
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    decoder = json.JSONDecoder()

    def parse_candidate(candidate: str) -> Dict[str, Any]:
        candidate = candidate.strip()
        if not candidate:
            return {}
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        for match in re.finditer(r"\{", candidate):
            try:
                parsed, _ = decoder.raw_decode(candidate[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    parsed = parse_candidate(text)
    if parsed:
        return parsed

    for fenced in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL):
        parsed = parse_candidate(fenced.group(1))
        if parsed:
            return parsed

    return {}


def _build_interaction_judge_prompt(
    *,
    policy_instruction: str,
    latest_user_message: str,
    latest_assistant_output: str,
    interaction_history: List[Dict[str, Any]],
) -> str:
    history_lines: list[str] = []
    for event in interaction_history[-5:]:
        history_lines.append(
            "turn={turn}, source={source}, reply={reply}, reason={reason}".format(
                turn=event.get("turn", ""),
                source=event.get("source", ""),
                reply=event.get("reply", ""),
                reason=event.get("reason", ""),
            )
        )
    history_text = "\n".join(history_lines) if history_lines else "无"
    return (
        "你是 ClawBench 的交互控制器。\n"
        "你的任务是判断是否需要模拟用户继续回复一条消息。\n\n"
        "只返回 JSON：\n"
        "{\n"
        '  "should_reply": boolean,\n'
        '  "reply": string,\n'
        '  "reason": string\n'
        "}\n\n"
        "交互策略：\n"
        f"{policy_instruction}\n\n"
        "最新用户消息：\n"
        f"{latest_user_message}\n\n"
        "最新 assistant 输出：\n"
        f"{latest_assistant_output}\n\n"
        "最近交互历史：\n"
        f"{history_text}\n\n"
        "规则：\n"
        "- 只有 assistant 明确在等待用户输入时，才回复。\n"
        "- 不要帮助 assistant 完成 benchmark 任务。\n"
        "- 除非交互策略明确要求，否则不要提供额外信息。\n"
        "- 如果不确定，返回 should_reply=false。\n"
    )


def _ensure_interaction_judge_agent(
    *,
    judge_agent_prefix: str,
    judge_model: str,
) -> str:
    agent_id = task_scoped_agent_id(f"{judge_agent_prefix}-{slugify_model(judge_model)}")
    workspace = Path("/tmp/pinchbench/judge/workspace")
    ensure_agent_exists(agent_id, judge_model, workspace)
    return agent_id


def _call_interaction_judge(
    *,
    prompt: str,
    judge_model: str,
    judge_agent_prefix: str,
    judge_backend: str,
    judge_base_url: Optional[str],
    judge_api_key: Optional[str],
    judge_timeout_seconds: float,
) -> tuple[Dict[str, Any], str]:
    if judge_backend == "api":
        judge_result = call_judge_api(
            prompt=prompt,
            model=judge_model,
            timeout_seconds=judge_timeout_seconds,
            base_url=judge_base_url,
            api_key=judge_api_key,
        )
        if judge_result.get("status") != "success":
            return {}, str(judge_result.get("error") or judge_result.get("status") or "judge api failed")
        parsed = _extract_json_object(judge_result.get("text", ""))
        return parsed, "" if parsed else "judge api returned invalid JSON"

    agent_id = _ensure_interaction_judge_agent(
        judge_agent_prefix=judge_agent_prefix,
        judge_model=judge_model,
    )
    judge_result = run_openclaw_prompt(
        agent_id=agent_id,
        prompt=prompt,
        workspace=Path("/tmp/pinchbench/judge/interaction"),
        timeout_seconds=judge_timeout_seconds,
    )
    if judge_result.get("status") != "success":
        return {}, str(judge_result.get("stderr") or judge_result.get("status") or "judge agent failed")
    text = _collect_messages_text(judge_result.get("transcript", []), "assistant")
    if not text:
        text = str(judge_result.get("stdout", ""))
    parsed = _extract_json_object(text)
    if not parsed:
        logger.warning(
            "Interaction judge returned invalid JSON. output preview:\n%s",
            _preview_subprocess_output(text, 4000),
        )
    return parsed, "" if parsed else "judge agent returned invalid JSON"


def _execute_interactive_openclaw_task(
    *,
    task: Task,
    agent_id: str,
    session_id: str,
    workspace: Path,
    started_at: float,
    timeout_seconds: float,
    interactions: Dict[str, Any],
    judge_model: str,
    judge_agent_prefix: str,
    judge_backend: str,
    judge_base_url: Optional[str],
    judge_api_key: Optional[str],
    judge_timeout_seconds: float,
) -> Dict[str, Any]:
    stdout = ""
    stderr = ""
    exit_code = -1
    timed_out = False
    interaction_events: list[dict[str, Any]] = []
    last_event_count = 0
    message = task.prompt
    max_turns = int(interactions.get("max_turns", 10))
    rules = interactions.get("rules", [])
    judge_policy = interactions.get("judge_policy")

    logger.info(
        "[interaction] task=%s session=%s configured=true max_turns=%s rules=%d judge_policy=%s",
        task.task_id,
        session_id,
        max_turns,
        len(rules),
        bool(judge_policy),
    )

    if judge_policy and judge_backend != "api":
        try:
            judge_agent_id = _ensure_interaction_judge_agent(
                judge_agent_prefix=judge_agent_prefix,
                judge_model=judge_model,
            )
            logger.info(
                "[interaction] task=%s prepared judge agent=%s model=%s",
                task.task_id,
                judge_agent_id,
                judge_model,
            )
        except Exception as exc:
            logger.warning("[interaction] failed to prepare judge agent: %s", exc)

    turn = 0
    interaction_turns = 0
    while True:
        turn += 1
        elapsed = time.time() - started_at
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            timed_out = True
            logger.info("[interaction] task=%s session=%s stopped reason=timeout", task.task_id, session_id)
            break

        logger.info(
            '[interaction] task=%s session=%s turn=%d sent_message_preview="%s"',
            task.task_id,
            session_id,
            turn,
            _preview_text(message),
        )
        turn_started = time.time()
        turn_stdout = ""
        turn_stderr = ""
        try:
            result = _run_openclaw_message(
                agent_id=agent_id,
                session_id=session_id,
                message=message,
                workspace=workspace,
                timeout_seconds=remaining,
            )
            turn_stdout = result.stdout
            turn_stderr = result.stderr
            stdout += result.stdout
            stderr += result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            turn_stdout = _coerce_subprocess_output(exc.stdout)
            turn_stderr = _coerce_subprocess_output(exc.stderr)
            stdout += turn_stdout
            stderr += turn_stderr
            exit_code = -1
        except FileNotFoundError as exc:
            turn_stderr = f"openclaw command not found: {exc}"
            stderr += turn_stderr
            exit_code = -1

        transcript, _ = _load_transcript(agent_id, session_id, started_at)
        new_events = transcript[last_event_count:]
        last_event_count = len(transcript)
        assistant_text = _collect_messages_text(new_events, "assistant")
        if not assistant_text:
            assistant_text = _strip_ansi((turn_stdout or "") + "\n" + (turn_stderr or ""))

        logger.info(
            '[interaction] task=%s session=%s turn=%d assistant_output_preview="%s"',
            task.task_id,
            session_id,
            turn,
            _preview_text(assistant_text),
        )

        reply = ""
        candidate_reply = ""
        matched_rule: Dict[str, Any] | None = None
        judge_decision: Dict[str, Any] | None = None
        source = "none"
        reason = "no_reply"

        if not timed_out and exit_code in (0, -1):
            candidate_reply, matched_rule = _match_interaction_reply(assistant_text, rules)
            if candidate_reply:
                source = "rule"
                reason = "matched on_output_contains"
            elif judge_policy:
                policy_instruction = str(judge_policy.get("instruction") or "").strip()
                if policy_instruction:
                    judge_prompt = _build_interaction_judge_prompt(
                        policy_instruction=policy_instruction,
                        latest_user_message=message,
                        latest_assistant_output=assistant_text,
                        interaction_history=interaction_events,
                    )
                    try:
                        judge_decision, judge_error = _call_interaction_judge(
                            prompt=judge_prompt,
                            judge_model=judge_model,
                            judge_agent_prefix=judge_agent_prefix,
                            judge_backend=judge_backend,
                            judge_base_url=judge_base_url,
                            judge_api_key=judge_api_key,
                            judge_timeout_seconds=judge_timeout_seconds,
                        )
                    except Exception as exc:
                        judge_decision = {}
                        judge_error = str(exc)
                    if judge_decision and judge_decision.get("should_reply") and judge_decision.get("reply"):
                        candidate_reply = str(judge_decision.get("reply"))
                        source = "judge_policy"
                        reason = str(judge_decision.get("reason") or "judge_policy should_reply=true")
                    else:
                        source = "judge_policy"
                        reason = judge_error or str(
                            (judge_decision or {}).get("reason") or "judge_policy should_reply=false"
                        )
                else:
                    source = "judge_policy"
                    reason = "judge_policy instruction missing"
            if candidate_reply:
                if interaction_turns >= max_turns:
                    reason = "max_turns"
                    reply = ""
                else:
                    reply = candidate_reply
                    interaction_turns += 1
        elif timed_out:
            source = "error"
            reason = "timeout"
        else:
            source = "error"
            reason = f"openclaw exit {exit_code}"

        event = {
            "turn": turn,
            "sent_message": message,
            "new_assistant_output": assistant_text,
            "source": source,
            "matched_rule": matched_rule.get("on_output_contains", "") if matched_rule else "",
            "judge_decision": judge_decision,
            "reply": reply,
            "reason": reason,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "elapsed_seconds": round(time.time() - turn_started, 2),
        }
        interaction_events.append(event)

        if source == "rule":
            logger.info(
                '[interaction] task=%s session=%s turn=%d source=rule matched="%s" reply="%s"',
                task.task_id,
                session_id,
                turn,
                event["matched_rule"],
                _preview_text(reply, 200),
            )
        elif source == "judge_policy":
            logger.info(
                '[interaction] task=%s session=%s turn=%d source=judge_policy should_reply=%s reply="%s" reason="%s"',
                task.task_id,
                session_id,
                turn,
                bool(judge_decision and judge_decision.get("should_reply")),
                _preview_text(reply, 200),
                _preview_text(reason, 400),
            )

        if timed_out or exit_code not in (0, -1):
            logger.info("[interaction] task=%s session=%s stopped reason=%s", task.task_id, session_id, reason)
            break
        if not reply:
            logger.info("[interaction] task=%s session=%s stopped reason=%s", task.task_id, session_id, reason)
            break
        message = reply

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "interaction_events": interaction_events,
    }


def execute_openclaw_task(
    *,
    task: Task,
    agent_id: str,
    model_id: str,
    run_id: str,
    timeout_multiplier: float,
    skill_dir: Path,
    output_dir: Optional[Path] = None,
    verbose: bool = False,
    copy_main_workspace_md: bool = False,
    link_skills: bool = False,
    judge_model: str = DEFAULT_INTERACTION_JUDGE_MODEL,
    judge_agent_prefix: str = DEFAULT_INTERACTION_JUDGE_AGENT_PREFIX,
    judge_backend: str = "openclaw",
    judge_base_url: Optional[str] = None,
    judge_api_key: Optional[str] = None,
    judge_timeout_seconds: float = 180.0,
) -> Dict[str, Any]:
    logger.info("🤖 Agent [%s] starting task: %s", agent_id, task.task_id)
    logger.info("   Task: %s", task.name)
    logger.info("   Category: %s", task.category)
    if verbose:
        logger.info(
            "   Prompt: %s", task.prompt[:500] + "..." if len(task.prompt) > 500 else task.prompt
        )

    # Clean up previous session transcripts so we can reliably find this task's
    # transcript (OpenClaw uses its own UUID-based naming, not our session ID).
    cleanup_agent_sessions(agent_id)

    start_time = time.time()
    workspace = prepare_task_workspace(
        skill_dir, run_id, task, agent_id,
        copy_main_workspace_md=copy_main_workspace_md,
        link_skills=link_skills,
    )
    session_id = f"{task.task_id}_{int(time.time() * 1000)}"
    timeout_seconds = task.timeout_seconds * timeout_multiplier
    stdout = ""
    stderr = ""
    exit_code = -1
    timed_out = False
    interaction_events: list[dict[str, Any]] = []
    sessions = task.frontmatter.get("sessions", [])
    interactions = _normalize_interactions(task.frontmatter)

    if interactions:
        if not task.prompt.strip():
            logger.warning("OpenClaw message is empty: task=%s", task.task_id)
        interactive_result = _execute_interactive_openclaw_task(
            task=task,
            agent_id=agent_id,
            session_id=session_id,
            workspace=workspace,
            started_at=start_time,
            timeout_seconds=timeout_seconds,
            interactions=interactions,
            judge_model=judge_model,
            judge_agent_prefix=judge_agent_prefix,
            judge_backend=judge_backend,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            judge_timeout_seconds=judge_timeout_seconds,
        )
        stdout = interactive_result["stdout"]
        stderr = interactive_result["stderr"]
        exit_code = interactive_result["exit_code"]
        timed_out = interactive_result["timed_out"]
        interaction_events = interactive_result.get("interaction_events", [])
    # Check if this is a multi-session task
    elif sessions:
        # Multi-session task: send each prompt in sequence
        logger.info("📋 Multi-session task with %d sessions", len(sessions))
        for i, session_entry in enumerate(sessions, 1):
            # Extract prompt text from session entry (handle both string and dict formats)
            if isinstance(session_entry, str):
                session_prompt = session_entry
            elif isinstance(session_entry, dict):
                session_prompt = session_entry.get("prompt") or session_entry.get("message", "")
            else:
                logger.warning("⚠️ Skipping invalid session entry: %s", session_entry)
                continue
            if not session_prompt.strip():
                logger.warning(
                    "OpenClaw message is empty: task=%s session=%d/%d",
                    task.task_id,
                    i,
                    len(sessions),
                )

            logger.info("   Session %d/%d", i, len(sessions))
            elapsed = time.time() - start_time
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                timed_out = True
                break
            try:
                result = _run_openclaw_message(
                    agent_id=agent_id,
                    session_id=session_id,
                    message=session_prompt,
                    workspace=workspace,
                    timeout_seconds=remaining,
                )
                stdout += result.stdout
                stderr += result.stderr
                exit_code = result.returncode
                if result.returncode not in (0, -1):
                    break
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout += _coerce_subprocess_output(exc.stdout)
                stderr += _coerce_subprocess_output(exc.stderr)
                break
            except FileNotFoundError as exc:
                stderr = f"openclaw command not found: {exc}"
                break
    else:
        # Single-session task: send task.prompt once
        if not task.prompt.strip():
            logger.warning("OpenClaw message is empty: task=%s", task.task_id)
        try:
            result = _run_openclaw_message(
                agent_id=agent_id,
                session_id=session_id,
                message=task.prompt,
                workspace=workspace,
                timeout_seconds=timeout_seconds,
            )
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _coerce_subprocess_output(exc.stdout)
            stderr = _coerce_subprocess_output(exc.stderr)
        except FileNotFoundError as exc:
            stderr = f"openclaw command not found: {exc}"

    if task.frontmatter.get("benchmark_kind") == "workflow":
        # Try to extract flow_id from the agent's tool call results first
        # (more reliable than DB/FS discovery which gets confused by concurrent flows)
        workflow_id = task.frontmatter.get("workflow_id", "")
        explicit_flow_id = _extract_workflow_flow_id(agent_id, session_id, start_time, workflow_id)
        transcript, transcript_path = _load_workflow_transcript(
            agent_id, session_id, start_time, task,
            explicit_flow_id=explicit_flow_id,
        )
    else:
        transcript, transcript_path = _load_transcript(agent_id, session_id, start_time)
    usage = _extract_usage_from_transcript(transcript)
    execution_time = time.time() - start_time

    # Archive the raw transcript JSONL before cleanup_agent_sessions deletes it
    if transcript_path and output_dir:
        import shutil as _shutil
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_dest = output_dir / f"{task.task_id}.jsonl"
        try:
            _shutil.copy2(transcript_path, archive_dest)
            logger.info("Archived transcript to %s", archive_dest)
        except OSError as exc:
            logger.warning("Failed to archive transcript: %s", exc)

    status = "success"
    if timed_out:
        status = "timeout"
    if not transcript:
        status = "error"
    if exit_code not in (0, -1) and not timed_out:
        status = "error"
    if stderr and "openclaw command not found" in str(stderr):
        status = "error"

    if status == "error" and not transcript:
        logger.warning(
            "OpenClaw task produced no transcript: agent=%s session=%s exit_code=%s timed_out=%s workspace=%s",
            agent_id,
            session_id,
            exit_code,
            timed_out,
            workspace,
        )
        if stdout:
            logger.warning("OpenClaw stdout preview:\n%s", _preview_subprocess_output(stdout))
        if stderr:
            logger.warning("OpenClaw stderr preview:\n%s", _preview_subprocess_output(stderr))

    # Verbose logging for debugging
    if verbose:
        logger.info("   [VERBOSE] Exit code: %s", exit_code)
        logger.info("   [VERBOSE] Execution time: %.2fs", execution_time)
        logger.info("   [VERBOSE] Workspace: %s", workspace)
        if stdout:
            logger.info("   [VERBOSE] Stdout (first 1000 chars):\n%s", stdout[:1000])
        if stderr:
            logger.info("   [VERBOSE] Stderr:\n%s", stderr[:1000])
        logger.info("   [VERBOSE] Transcript entries: %d", len(transcript))

        # Show agent responses from transcript
        for entry in transcript:
            if entry.get("type") == "message":
                msg = entry.get("message", {})
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if role == "assistant":
                    # Truncate long responses
                    preview = content[:500] + "..." if len(content) > 500 else content
                    logger.info("   [VERBOSE] Agent response: %s", preview)
                elif role == "user":
                    preview = content[:200] + "..." if len(content) > 200 else content
                    logger.info("   [VERBOSE] User message: %s", preview)

        # Show workspace files after task
        if workspace.exists():
            logger.info("   [VERBOSE] Workspace files after task:")
            for f in sorted(workspace.rglob("*")):
                if f.is_file():
                    try:
                        size = f.stat().st_size
                        logger.info("      %s (%d bytes)", f.relative_to(workspace), size)
                    except OSError:
                        logger.info("      %s", f.relative_to(workspace))

    return {
        "agent_id": agent_id,
        "task_id": task.task_id,
        "status": status,
        "transcript": transcript,
        "usage": usage,
        "workspace": str(workspace),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "execution_time": execution_time,
        "stdout": stdout,
        "stderr": stderr,
        "interaction_events": interaction_events,
    }


def run_openclaw_prompt(
    *,
    agent_id: str,
    prompt: str,
    workspace: Path,
    timeout_seconds: float,
) -> Dict[str, Any]:
    """Run a single OpenClaw prompt for helper agents like the judge."""
    cleanup_agent_sessions(agent_id)

    agent_workspace = _get_agent_workspace(agent_id)
    if agent_workspace and agent_workspace.exists():
        logger.debug("Using helper agent workspace without removing bootstrap files: %s", agent_workspace)

    start_time = time.time()
    workspace.mkdir(parents=True, exist_ok=True)
    session_id = f"judge_{int(time.time() * 1000)}"
    stdout = ""
    stderr = ""
    exit_code = -1
    timed_out = False

    chunks = [
        prompt[i : i + JUDGE_MAX_MSG_CHARS]
        for i in range(0, max(1, len(prompt)), JUDGE_MAX_MSG_CHARS)
    ]
    if len(chunks) > 1:
        total_chunks = len(chunks)
        chunks = [
            (
                f"You are receiving a long prompt in {total_chunks} parts.\n"
                f"Ignore and do not respond until the final part.\n\n"
                f"Part 1/{total_chunks}:\n{chunks[0]}"
            )
        ] + [
            (
                f"Part {i + 2}/{total_chunks}:\n{chunks[i + 1]}"
                if i + 2 < total_chunks
                else (
                    f"Part {i + 2}/{total_chunks} (final):\n{chunks[i + 1]}\n"
                    "All parts received. Proceed with final judgment now."
                )
            )
            for i in range(0, total_chunks - 1)
        ]
    for chunk in chunks:
        elapsed = time.time() - start_time
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            timed_out = True
            break
        try:
            result = run_openclaw_agent(
                agent_id=agent_id,
                session_id=session_id,
                message=chunk,
                workspace=workspace,
                timeout_seconds=remaining,
                runner=subprocess.run,
                use_shell=USE_SHELL,
            )
            stdout += result.stdout
            stderr += result.stderr
            exit_code = result.returncode
            if result.returncode not in (0, -1) and not timed_out:
                break
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout += _coerce_subprocess_output(exc.stdout)
            stderr += _coerce_subprocess_output(exc.stderr)
            break
        except FileNotFoundError as exc:
            stderr += f"openclaw command not found: {exc}"
            break

    transcript, _ = _load_transcript(agent_id, session_id, start_time)
    execution_time = time.time() - start_time

    status = "success"
    if timed_out:
        status = "timeout"
    if not transcript:
        status = "error"
    if exit_code not in (0, -1) and not timed_out:
        status = "error"
    if stderr and "openclaw command not found" in str(stderr):
        status = "error"

    return {
        "agent_id": agent_id,
        "status": status,
        "transcript": transcript,
        "workspace": str(workspace),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "execution_time": execution_time,
        "stdout": stdout,
        "stderr": stderr,
    }


_JUDGE_SYSTEM_MSG = (
    "You are a strict grading function. "
    "Respond with ONLY a JSON object, no prose, no markdown fences, no extra text."
)


def call_judge_api(
    *,
    prompt: str,
    model: str,
    timeout_seconds: float = 120.0,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Call a judge model directly via API, bypassing OpenClaw.

    Dispatches based on model prefix:
      - openrouter/* -> OpenRouter chat completions API
      - anthropic/*  -> Anthropic Messages API
      - openai/*     -> OpenAI chat completions API
      - claude       -> headless Claude CLI (claude -p)

    If base_url is provided, uses custom OpenAI-compatible endpoint instead.

    Returns {"status": str, "text": str, "error"?: str}.
    """
    # Custom OpenAI-compatible endpoint takes precedence
    if base_url:
        key = api_key if api_key else os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return {"status": "error", "text": "", "error": "API key not provided (set --judge-api-key or OPENAI_API_KEY)"}
        return _judge_via_openai_compat(
            prompt, model,
            f"{base_url.rstrip('/')}/chat/completions",
            key, timeout_seconds,
        )

    if model == "claude" or model.startswith("claude:"):
        return _judge_via_claude_cli(prompt, model, timeout_seconds)
    if model.startswith("anthropic/"):
        return _judge_via_anthropic(prompt, model, timeout_seconds)
    if model.startswith("openai/"):
        return _judge_via_openai(prompt, model, timeout_seconds)
    # Default: OpenRouter (handles openrouter/ prefix and bare provider/model)
    return _judge_via_openrouter(prompt, model, timeout_seconds)


def _judge_via_openai_compat(
    prompt: str,
    api_model: str,
    endpoint: str,
    api_key: str,
    timeout_seconds: float,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Shared implementation for OpenAI-compatible chat completions APIs."""
    # Use larger max_tokens for models that generate reasoning_content (e.g., Kimi-K2.5)
    # to avoid content being truncated after reasoning
    payload = json.dumps({
        "model": api_model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 4096,
    }).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.error("Judge API error (%s): %s", exc.code, body)
        return {"status": "error", "text": "", "error": f"HTTP {exc.code}: {body}"}
    except error.URLError as exc:
        logger.error("Judge network error: %s", exc)
        return {"status": "error", "text": "", "error": str(exc)}
    except TimeoutError:
        return {"status": "timeout", "text": "", "error": "Request timed out"}

    choices = data.get("choices", [])
    if not choices:
        return {"status": "error", "text": "", "error": "No choices in response"}
    text = choices[0].get("message", {}).get("content", "")
    return {"status": "success", "text": text}


def _judge_via_openrouter(prompt: str, model: str, timeout_seconds: float) -> Dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"status": "error", "text": "", "error": "OPENROUTER_API_KEY not set"}
    bare_model = model.removeprefix("openrouter/")
    return _judge_via_openai_compat(
        prompt, bare_model,
        "https://openrouter.ai/api/v1/chat/completions",
        api_key, timeout_seconds,
        extra_headers={"HTTP-Referer": "https://pinchbench.com", "X-Title": "PinchBench-Judge"},
    )


def _judge_via_openai(prompt: str, model: str, timeout_seconds: float) -> Dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"status": "error", "text": "", "error": "OPENAI_API_KEY not set"}
    bare_model = model.removeprefix("openai/")
    return _judge_via_openai_compat(
        prompt, bare_model,
        "https://api.openai.com/v1/chat/completions",
        api_key, timeout_seconds,
    )


def _judge_via_anthropic(prompt: str, model: str, timeout_seconds: float) -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"status": "error", "text": "", "error": "ANTHROPIC_API_KEY not set"}
    bare_model = model.removeprefix("anthropic/")
    payload = json.dumps({
        "model": bare_model,
        "max_tokens": 4096,
        "temperature": 0.0,
        "system": _JUDGE_SYSTEM_MSG,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    req = request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload, headers=headers, method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.error("Anthropic judge API error (%s): %s", exc.code, body)
        return {"status": "error", "text": "", "error": f"HTTP {exc.code}: {body}"}
    except error.URLError as exc:
        logger.error("Anthropic judge network error: %s", exc)
        return {"status": "error", "text": "", "error": str(exc)}
    except TimeoutError:
        return {"status": "timeout", "text": "", "error": "Request timed out"}

    content = data.get("content", [])
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
    return {"status": "success", "text": text}


def _judge_via_claude_cli(prompt: str, model: str, timeout_seconds: float) -> Dict[str, Any]:
    """Use headless Claude CLI (claude -p) as judge."""
    cmd: List[str] = ["claude", "-p"]
    # Support "claude:model-name" to pass --model
    if ":" in model:
        _, cli_model = model.split(":", 1)
        cmd.extend(["--model", cli_model])
    try:
        result = subprocess.run(
            cmd,
            input=f"{_JUDGE_SYSTEM_MSG}\n\n{prompt}",
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {"status": "error", "text": "", "error": "claude CLI not found"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "text": "", "error": "claude -p timed out"}
    if result.returncode != 0:
        return {"status": "error", "text": "", "error": f"claude exit {result.returncode}: {result.stderr[:300]}"}
    return {"status": "success", "text": result.stdout}
