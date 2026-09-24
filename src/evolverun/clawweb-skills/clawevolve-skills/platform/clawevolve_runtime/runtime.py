#!/usr/bin/env python3
"""Runtime wrapper for real ClawEvolve Stage extensions and Skill candidates."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_HTTP_BYTES = 64 * 1024 * 1024
MAX_FILES = 2_000
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_FIXTURE_BYTES = 1024 * 1024


class RuntimeFailure(RuntimeError):
    pass


def _runtime_layout_home(script_path: Path | None = None) -> Path:
    configured = os.environ.get("ENGINE_RUNTIME_LAYOUT_HOME", "").strip()
    if configured:
        home = Path(configured).expanduser()
    else:
        resolved_script = (script_path or Path(__file__)).resolve()
        home = next(
            (
                ancestor.parent.parent
                for ancestor in resolved_script.parents
                if ancestor.name == "workspace"
                and ancestor.parent.name in {".openclaw", "openclaw"}
            ),
            Path("/home/admin"),
        )
    if not home.is_absolute():
        raise RuntimeFailure("ENGINE_RUNTIME_LAYOUT_HOME must be absolute")
    return home.resolve()


RUNTIME_LAYOUT_HOME = _runtime_layout_home()
LOGICAL_RUNTIME_HOME = Path("/home/admin")
SOURCE_WORKSPACE = RUNTIME_LAYOUT_HOME / ".openclaw" / "workspace"
CANDIDATE_ROOT = RUNTIME_LAYOUT_HOME / ".openclaw" / "clawevolve_workspaces"


def _runtime_path(value: Any) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute():
        raise RuntimeFailure("runtime path must be absolute")
    try:
        relative = path.relative_to(LOGICAL_RUNTIME_HOME)
    except ValueError:
        return path.resolve()
    return (RUNTIME_LAYOUT_HOME / relative).resolve()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeFailure(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise RuntimeFailure(f"invalid JSON number: {value}")


def _read_json(path: Path, *, strict: bool = False) -> dict[str, Any]:
    try:
        # Strict parsing is opt-in for Stage result files, not a change to
        # candidate lifecycle or the frozen platform input protocols.
        options = {"object_pairs_hook": _unique_json_object, "parse_constant": _reject_json_constant} if strict else {}
        source = path.read_text(encoding="utf-8")
        value = json.loads(source, **options)
    except Exception as exc:
        raise RuntimeFailure(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeFailure(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalized_sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise RuntimeFailure("invalid SHA-256")
    return text


def _base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    local = parsed.hostname in {"127.0.0.1", "localhost"}
    if (
        parsed.scheme not in ({"http", "https"} if local else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeFailure("invalid clawweb-url")
    return raw


def _step_url(args: argparse.Namespace, suffix: str) -> str:
    task = urllib.parse.quote(args.task_id, safe="")
    step = urllib.parse.quote(args.step_id, safe="")
    return f"{_base_url(args.clawweb_url)}/api/evolve/internal/tasks/{task}/steps/{step}/{suffix}"


def _http(method: str, url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None,
          max_bytes: int = MAX_HTTP_BYTES) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=300) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise RuntimeFailure("HTTP response exceeds safety limit")
    return payload


def _step_input(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(_http("GET", _step_url(args, "input")))
    if not isinstance(payload, dict):
        raise RuntimeFailure("Step Input must be an object")
    return payload


def _report(
    args: argparse.Namespace,
    status: str,
    summary: str,
    *,
    output: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status, "summary": summary}
    if output is not None:
        value["output"] = output
    if error is not None:
        value["error"] = error
    if progress is not None:
        value["progress"] = progress
    args._report_attempted = True
    try:
        raw = _http(
            "POST",
            _step_url(args, "report"),
            body=_json(value).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    except urllib.error.HTTPError as exc:
        # These responses reject the payload before accepting the report. Only
        # this definite rejection permits a subsequent failure report; timeout,
        # 5xx and state conflicts remain uncertain and must not be overwritten.
        if exc.code in (400, 422):
            args._report_attempted = False
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeFailure(f"ClawWeb report rejected (HTTP {exc.code}): {detail}") from exc
    response = json.loads(raw or b"{}")
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeFailure("ClawWeb did not acknowledge the Stage report")
    return response


def _run_dir(args: argparse.Namespace) -> Path:
    return SOURCE_WORKSPACE / "clawevolve_results" / args.task_id / "stage_runtime" / args.step_id


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/"):
        raise RuntimeFailure(f"unsafe ZIP path: {name}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeFailure(f"unsafe ZIP path: {name}")
    return path


def _extract_zip(payload: bytes, destination: Path) -> None:
    if len(payload) > MAX_HTTP_BYTES:
        raise RuntimeFailure("ZIP exceeds safety limit")
    destination.mkdir(parents=True, exist_ok=False)
    count = 0
    expanded = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            path = _safe_member(info.filename.rstrip("/"))
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeFailure("ZIP symlinks are not allowed")
            if info.is_dir():
                (destination / Path(*path.parts)).mkdir(parents=True, exist_ok=True)
                continue
            count += 1
            expanded += info.file_size
            if count > MAX_FILES or expanded > MAX_EXPANDED_BYTES:
                raise RuntimeFailure("expanded ZIP exceeds safety limit")
            target = destination / Path(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
def _candidate_paths(payload: dict[str, Any], task_id: str) -> tuple[Path, Path]:
    workspace = _runtime_path(payload.get("candidateWorkspace"))
    target = _runtime_path((payload.get("targetSkill") or {}).get("path"))
    expected_workspace = (CANDIDATE_ROOT / task_id / "workspace").resolve()
    if workspace != expected_workspace:
        raise RuntimeFailure("candidate workspace does not match task boundary")
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeFailure("target Skill is outside candidate workspace") from exc
    return workspace, target


def _copy_source_workspace(source: Path, destination: Path) -> None:
    if source.resolve() != SOURCE_WORKSPACE.resolve() or not source.is_dir():
        raise RuntimeFailure("source workspace is unavailable")
    if destination.exists():
        if destination.is_symlink():
            raise RuntimeFailure("candidate workspace must not be a symlink")
        # The directory is task-scoped and has already passed _candidate_paths.
        # Without the marker it can only be an interrupted prepare, so rebuild it
        # from the frozen package instead of leaving the task permanently stuck.
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.incoming.{os.getpid()}"
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(
        source,
        temporary,
        symlinks=True,
        ignore=shutil.ignore_patterns("clawevolve_results", ".nfs*"),
    )
    os.replace(temporary, destination)


def _replace_directory(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    incoming = target.parent / f".{target.name}.incoming.{os.getpid()}"
    backup = target.parent / f".{target.name}.backup.{os.getpid()}"
    if incoming.exists():
        shutil.rmtree(incoming)
    shutil.copytree(source, incoming, symlinks=False)
    if target.exists() or target.is_symlink():
        os.replace(target, backup)
    try:
        os.replace(incoming, target)
    except Exception:
        if backup.exists() or backup.is_symlink():
            os.replace(backup, target)
        raise
    if backup.exists() or backup.is_symlink():
        if backup.is_dir() and not backup.is_symlink():
            shutil.rmtree(backup)
        else:
            backup.unlink()


def _activate_candidate_skill(workspace: Path, target: Path) -> Path:
    """Point the target Skill's discovery entry at the task-local candidate.

    Source workspaces may contain absolute discovery links managed by the host.  A
    byte-for-byte workspace copy must not retain that link for the Skill being
    evolved, otherwise an Agent following ``workspace/skills/<name>`` can edit
    the live Skill package before acceptance.
    """
    relative = target.relative_to(workspace)
    if len(relative.parts) == 3 and relative.parts[:2] == ("skills", "skills-local"):
        name = relative.parts[2]
        link = workspace / "skills" / name
        link_target = Path("skills-local") / name
    elif len(relative.parts) == 2 and relative.parts[0] == "skills-local":
        name = relative.parts[1]
        link = workspace / "skills" / name
        link_target = Path("..") / "skills-local" / name
    else:
        raise RuntimeFailure("target Skill must use a supported private Skill layout")

    if link == target:
        raise RuntimeFailure("target Skill discovery entry overlaps package directory")
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(link_target, target_is_directory=True)
    if link.resolve() != target.resolve():
        raise RuntimeFailure("candidate Skill discovery entry is not task-local")
    return link


def _ensure_task_results_link(workspace: Path) -> None:
    """Share task-scoped flow artifacts without sharing the candidate Skill."""
    source_results = SOURCE_WORKSPACE / "clawevolve_results"
    source_results.mkdir(parents=True, exist_ok=True)
    candidate_results = workspace / "clawevolve_results"
    if candidate_results.is_symlink():
        if candidate_results.resolve() != source_results.resolve():
            raise RuntimeFailure("candidate results link points outside the task runtime")
        return
    if candidate_results.exists():
        raise RuntimeFailure("candidate results path must be the managed runtime link")
    candidate_results.symlink_to(source_results, target_is_directory=True)


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    payload = _step_input(args)
    if payload.get("protocolVersion") != "clawevolve.skill-candidate/v1" or payload.get("action") != "prepare":
        raise RuntimeFailure("unexpected prepare protocol")
    workspace, target = _candidate_paths(payload, args.task_id)
    marker = workspace / ".clawevolve-candidate.json"
    baseline = _normalized_sha256((payload.get("targetSkill") or {}).get("baselineSha256"))
    marker_value = {"taskId": args.task_id, "targetSkillPath": str(target), "baselineSha256": baseline}
    if marker.is_file():
        if _read_json(marker) != marker_value or not target.is_dir():
            raise RuntimeFailure("existing candidate does not match frozen task input")
    else:
        _copy_source_workspace(_runtime_path(payload.get("sourceWorkspace")), workspace)
        package = payload.get("targetSkill", {}).get("package", {})
        package_bytes = _http(str(package.get("method") or "GET"), str(package.get("url") or ""))
        if _sha256_bytes(package_bytes) != baseline:
            raise RuntimeFailure("baseline Skill package checksum mismatch")
        with tempfile.TemporaryDirectory(prefix="clawevolve-skill-prepare-") as temporary:
            extracted = Path(temporary) / "skill"
            _extract_zip(package_bytes, extracted)
            _replace_directory(extracted, target)
        _activate_candidate_skill(workspace, target)
        _atomic_json(marker, marker_value)
    # Reassert isolation for candidates prepared by older runtime versions and
    # for source workspaces whose managed discovery link changes over time.
    _activate_candidate_skill(workspace, target)
    _ensure_task_results_link(workspace)
    # ClawWeb owns the logical /home/admin contract paths.  Local runtimes may
    # map them to an isolated host directory for filesystem work, but the
    # report must preserve the frozen contract values so the coordinator can
    # verify that the same candidate was prepared.
    output = {
        "prepared": True,
        "workspace": str(payload.get("candidateWorkspace") or workspace),
        "targetSkillPath": str((payload.get("targetSkill") or {}).get("path") or target),
    }
    return {"report": _report(args, "succeeded", "候选 Skill 工作区已准备", output=output), "output": output}


def _canonical_zip(directory: Path) -> bytes:
    if not directory.is_dir():
        raise RuntimeFailure("target Skill directory does not exist")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temporary:
        path = Path(temporary.name)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            files = sorted(item for item in directory.rglob("*") if item.is_file() or item.is_symlink())
            if not files:
                raise RuntimeFailure("target Skill directory is empty")
            if len(files) > MAX_FILES:
                raise RuntimeFailure("target Skill has too many files")
            total = 0
            for item in files:
                if item.is_symlink():
                    raise RuntimeFailure("target Skill symlinks are not allowed")
                total += item.stat().st_size
                if total > MAX_EXPANDED_BYTES:
                    raise RuntimeFailure("target Skill exceeds size limit")
                info = zipfile.ZipInfo(item.relative_to(directory).as_posix())
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.external_attr = 0o100644 << 16
                archive.writestr(info, item.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _finalize(args: argparse.Namespace) -> dict[str, Any]:
    payload = _step_input(args)
    if payload.get("protocolVersion") != "clawevolve.skill-candidate/v1" or payload.get("action") != "finalize":
        raise RuntimeFailure("unexpected finalize protocol")
    workspace, target = _candidate_paths(payload, args.task_id)
    marker = _read_json(workspace / ".clawevolve-candidate.json")
    if marker.get("taskId") != args.task_id or marker.get("targetSkillPath") != str(target):
        raise RuntimeFailure("candidate marker does not match task")
    package_bytes = _canonical_zip(target)
    upload = payload.get("candidatePackage") or {}
    headers = {str(key): str(value) for key, value in (upload.get("headers") or {}).items()}
    _http(str(upload.get("method") or "PUT"), str(upload.get("url") or ""), body=package_bytes, headers=headers)
    artifact = {
        "ref": str(upload.get("ref") or ""),
        "size": len(package_bytes),
        "sha256": _sha256_bytes(package_bytes),
        "contentType": "application/zip",
    }
    output = {"artifact": artifact}
    return {"report": _report(args, "succeeded", "候选 Skill 已冻结，等待确认", output=output), "output": output}


def _stage_business_input(value: Any, task_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeFailure("Stage input must be an object")
    if "target_skill" not in value:
        return value
    target_input = value["target_skill"]
    if not isinstance(target_input, dict):
        raise RuntimeFailure("Stage target_skill must be an object")
    workspace, target = _candidate_paths({
        "candidateWorkspace": target_input.get("workspace"),
        "targetSkill": {"path": target_input.get("path")},
    }, task_id)
    expected = CANDIDATE_ROOT / task_id / "workspace"
    if any(path.is_symlink() for path in (CANDIDATE_ROOT, expected.parent, expected)):
        raise RuntimeFailure("candidate workspace must not be a symlink")
    if workspace.is_relative_to(SOURCE_WORKSPACE.resolve()):
        raise RuntimeFailure("Stage candidate must not use the source workspace")
    skill_file = target / "SKILL.md"
    if (not workspace.is_dir() or not target.is_dir() or not skill_file.is_file()
            or not skill_file.resolve().is_relative_to(workspace)):
        raise RuntimeFailure("Stage candidate is unavailable; do not search for a substitute")
    # Central input keeps portable protocol paths. Only the business-facing copy
    # uses this runtime's real filesystem paths, just as prepare/finalize do.
    return {**value, "target_skill": {
        **target_input, "workspace": str(workspace), "path": str(target),
    }}


def _materialize_loop_feedback(value: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    loop = value.get("loop")
    if not isinstance(loop, dict):
        return value
    feedback = loop.get("user_feedback")
    if not isinstance(feedback, dict):
        raise RuntimeFailure("loop.user_feedback must be an object")
    files = feedback.get("files", [])
    if not isinstance(files, list) or len(files) > 10:
        raise RuntimeFailure("loop.user_feedback.files is invalid")
    destination = run_dir / "feedback"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    materialized = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise RuntimeFailure(f"loop feedback file {index} is invalid")
        name = item.get("name")
        size = item.get("size")
        sha256 = item.get("sha256")
        download = item.get("download")
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name or len(name) > 255
                or type(size) is not int or not 0 <= size <= 20 * 1024 * 1024
                or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                or not isinstance(download, dict) or set(download) != {"method", "url"}
                or download.get("method") != "GET" or not isinstance(download.get("url"), str)):
            raise RuntimeFailure(f"loop feedback file {index} metadata is invalid")
        raw = _http("GET", download["url"], max_bytes=20 * 1024 * 1024)
        if len(raw) != size or _sha256_bytes(raw) != sha256:
            raise RuntimeFailure(f"loop feedback file {index} checksum mismatch")
        path = destination / name
        path.write_bytes(raw)
        materialized.append({
            "name": name,
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "size": size,
            "sha256": sha256,
            "path": str(path),
        })
    return {
        **value,
        "loop": {
            **loop,
            "user_feedback": {**feedback, "files": materialized},
        },
    }


def _fixture_safe_path(path: Path) -> None:
    # Check lexical ancestors before any resolve() can hide a symlink.
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeFailure("fixture path must be absolute and task-local")
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            # local_proc exposes this one platform-owned alias inside the same
            # Bot home. Do not resolve the entire path: doing so would hide
            # malicious links under workspace/task/lock directories.
            state_root = RUNTIME_LAYOUT_HOME / "openclaw"
            if (ancestor == RUNTIME_LAYOUT_HOME / ".openclaw"
                    and not state_root.is_symlink() and state_root.is_dir()
                    and ancestor.resolve(strict=True) == state_root):
                _fixture_safe_path(state_root)
                continue
            raise RuntimeFailure("fixture path contains a symlink")
        if ancestor.exists() and ancestor != path and not ancestor.is_dir():
            raise RuntimeFailure("fixture ancestor is not a directory")


def _fixture_mkdir(path: Path) -> None:
    _fixture_safe_path(path)
    path.mkdir(parents=True, exist_ok=True)
    _fixture_safe_path(path)


def _ensure_test_skill_fixture(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    resources = payload.get("resources")
    business = payload.get("input")
    target = business.get("target_skill") if isinstance(business, dict) else None
    if not isinstance(resources, dict) or "testSkillFixture" not in resources:
        if isinstance(target, dict) and target.get("kind") == "stage_test_fixture":
            raise RuntimeFailure("fixture target requires its frozen resource")
        return
    resource = resources["testSkillFixture"]
    task = business.get("task") if isinstance(business, dict) else None
    outer_task = payload.get("task")
    stage = payload.get("stage")
    if (not isinstance(task, dict) or not isinstance(outer_task, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}", args.task_id) or ".." in args.task_id
            or task.get("task_id") != args.task_id or outer_task.get("taskId") != args.task_id
            or task.get("task_type") != "stage_test" or outer_task.get("taskType") != "stage_test"
            or not isinstance(task.get("target_bot_id"), str) or not task["target_bot_id"].strip()
            or outer_task.get("targetBotId") != task["target_bot_id"]
            or not isinstance(stage, dict)
            or (stage.get("key"), stage.get("mode")) not in {
                ("diagnose", "preprocess"),
                ("plan", "replace"),
                ("hardening", "replace"),
            }):
        raise RuntimeFailure("fixture requires matching stage_test task identities and supported Stage mode")
    if (not isinstance(resource, dict)
            or set(resource) != {"kind", "fixtureId", "version", "taskId", "sha256", "package"}
            or resource.get("kind") != "stage_test_fixture"
            or not isinstance(resource.get("fixtureId"), str)
            or type(resource.get("version")) is not int
            or (resource.get("fixtureId"), resource["version"]) not in {
                ("skill-description-v1", 1), ("skill-description-v2", 2), ("skill-description-v3", 3),
            }
            or resource.get("taskId") != args.task_id
            or not isinstance(resource.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", resource["sha256"])):
        raise RuntimeFailure("invalid fixed fixture resource")
    digest = _normalized_sha256(resource["sha256"])
    fixture_id = resource["fixtureId"]
    # v1/v2 stay frozen. v3 is the control-plane's fixed daily-report input,
    # never a heuristic match against source paths or installed Bot assets.
    if resource["version"] == 3 and (
            outer_task.get("flow") != "skill_evolution"
            or (stage.get("key"), stage.get("mode")) != ("plan", "replace")
            or (payload.get("implementation") or {}).get("executionContract") != "clawevolve.plan-business/v1"):
        raise RuntimeFailure("fixture v3 requires explicit skill_evolution Plan business replacement")
    target_name = "daily-report-zh" if resource["version"] == 3 else "stage-test-text-summary"
    logical_workspace = LOGICAL_RUNTIME_HOME / ".openclaw/clawevolve_workspaces" / args.task_id / "workspace"
    relative_target = Path("skills/skills-local") / target_name
    expected_target = {
        "kind": "stage_test_fixture", "fixture_id": fixture_id,
        "asset_id": f"fixture:{args.task_id}:{fixture_id}", "skill_id": f"fixture:{fixture_id}",
        "name": target_name, "workspace": str(logical_workspace),
        "path": str(logical_workspace / relative_target), "baseline_sha256": digest,
    }
    if target != expected_target:
        raise RuntimeFailure("fixture target identity or path does not match frozen resource")
    package = resource["package"]
    if (not isinstance(package, dict) or set(package) != {"method", "url"}
            or package.get("method") != "GET" or not isinstance(package.get("url"), str)):
        raise RuntimeFailure("fixture requires a signed GET package")
    url = urllib.parse.urlsplit(package["url"])
    if (not url.hostname or url.username or url.password or url.fragment
            or url.scheme not in ({"http", "https"} if url.hostname in {"127.0.0.1", "localhost"} else {"https"})):
        raise RuntimeFailure("invalid fixture package URL")
    _fixture_safe_path(CANDIDATE_ROOT)
    _fixture_safe_path(_run_dir(args))
    task_root = CANDIDATE_ROOT / args.task_id
    _fixture_safe_path(task_root)
    if (_runtime_path(logical_workspace) != (task_root / "workspace").resolve()
            or task_root.resolve().is_relative_to(SOURCE_WORKSPACE.resolve())):
        raise RuntimeFailure("fixture root does not match isolated runtime boundary")
    lock_dir = CANDIDATE_ROOT / ".stage-test-fixture-locks"
    _fixture_mkdir(lock_dir)
    lock_path = lock_dir / f"{args.task_id}.lock"
    _fixture_safe_path(lock_path)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise RuntimeFailure("fixture lock must be a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        _fixture_safe_path(task_root)
        marker = task_root / ".stage-test-fixture.json"
        baseline = task_root / ".stage-test-fixture-baseline.zip"
        marker_value = {"kind": "stage_test_fixture", "fixtureId": fixture_id, "version": resource["version"],
                        "taskId": args.task_id, "sha256": digest, "target": str(Path("workspace") / relative_target)}
        if task_root.exists():
            # Never recover an unknown directory by overwriting it. Check the
            # immutable baseline, not the business-edited current Skill bytes.
            _fixture_safe_path(marker)
            _fixture_safe_path(baseline)
            if (not marker.is_file() or _read_json(marker, strict=True) != marker_value
                    or not baseline.is_file() or baseline.stat().st_size > MAX_FIXTURE_BYTES
                    or _sha256_bytes(baseline.read_bytes()) != digest):
                raise RuntimeFailure("existing fixture does not match frozen baseline")
            current = task_root / "workspace" / relative_target
            _fixture_safe_path(current / "SKILL.md")
            if not (current / "SKILL.md").is_file():
                raise RuntimeFailure("existing fixture Skill is unavailable")
            for item in task_root.rglob("*"):
                if item.is_symlink() or not (item.is_dir() or item.is_file()):
                    raise RuntimeFailure("existing fixture contains an unsafe filesystem entry")
            return
        raw = _http("GET", package["url"], max_bytes=MAX_FIXTURE_BYTES)
        if len(raw) > MAX_FIXTURE_BYTES or _sha256_bytes(raw) != digest:
            raise RuntimeFailure("fixture package size or checksum mismatch")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) != 1:
                raise RuntimeFailure("fixture ZIP must contain only root SKILL.md")
            info = infos[0]
            _safe_member(info.filename)
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if (info.filename != "SKILL.md" or info.orig_filename != "SKILL.md"
                    or info.is_dir() or file_type != stat.S_IFREG or info.external_attr & 0x10
                    or info.flag_bits & 1 or info.file_size > MAX_FIXTURE_BYTES):
                raise RuntimeFailure("invalid fixture ZIP member or expanded size")
            content = archive.read(info)
            if len(content) > MAX_FIXTURE_BYTES or not content.decode("utf-8").strip() or content.startswith(b"\xef\xbb\xbf"):
                raise RuntimeFailure("fixture SKILL.md must be nonempty UTF-8 without BOM")
        # Stage a whole new task tree, including its external marker/baseline,
        # then rename atomically. No source Workspace copy or candidate marker.
        with tempfile.TemporaryDirectory(prefix=".stage-fixture-incoming-", dir=CANDIDATE_ROOT) as temporary:
            incoming = Path(temporary) / "task"
            extracted = incoming / "workspace" / relative_target
            _extract_zip(raw, extracted)
            (incoming / baseline.name).write_bytes(raw)
            _atomic_json(incoming / marker.name, marker_value)
            _fixture_safe_path(task_root)
            if task_root.exists():
                raise RuntimeFailure("fixture task directory appeared during initialization")
            os.rename(incoming, task_root)


def _begin(args: argparse.Namespace, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or _step_input(args)
    if payload.get("protocolVersion") != "clawevolve.stage-runtime/v1":
        raise RuntimeFailure("unexpected Stage runtime protocol")
    execution_contract = (payload.get("implementation") or {}).get("executionContract")
    prior_context = _run_dir(args) / "context.json"
    if prior_context.is_file():
        prior_contract = _read_json(prior_context, strict=True).get("executionContract")
        if (prior_contract or execution_contract) and prior_contract != execution_contract:
            raise RuntimeFailure("frozen Stage execution contract changed on resume")
    if execution_contract not in (None, "", "clawevolve.plan-business/v1"):
        raise RuntimeFailure("unsupported Stage execution contract")
    _ensure_test_skill_fixture(args, payload)
    business_input = _stage_business_input(payload.get("input") or {}, args.task_id)
    implementation = payload.get("implementation") or {}
    package = implementation.get("package") or {}
    package_bytes = _http(str(package.get("method") or "GET"), str(package.get("url") or ""))
    if _sha256_bytes(package_bytes) != _normalized_sha256(implementation.get("packageSha256")):
        raise RuntimeFailure("Stage Skill package checksum mismatch")
    run_dir = _run_dir(args)
    business_input = _materialize_loop_feedback(business_input, run_dir)
    implementation_dir = run_dir / "package"
    if implementation_dir.exists():
        shutil.rmtree(implementation_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _extract_zip(package_bytes, implementation_dir)
    entrypoint = implementation_dir / str(implementation.get("entrypoint") or "")
    try:
        entrypoint.resolve().relative_to(implementation_dir.resolve())
    except ValueError as exc:
        raise RuntimeFailure("Stage Skill entrypoint escapes package") from exc
    if not entrypoint.is_file():
        raise RuntimeFailure("Stage Skill entrypoint is missing")
    input_file = run_dir / "input.json"
    result_file = run_dir / "result.json"
    _atomic_json(input_file, business_input)
    result_file.unlink(missing_ok=True)
    context = {
        "schemaVersion": "clawevolve.stage-agent-context/v1",
        "validationUrl": _step_url(args, "validate-output"),
        "stage": payload.get("stage"),
        "implementationSkill": str(entrypoint),
        "inputFile": str(input_file),
        "resultFile": str(result_file),
        **({"executionContract": execution_contract} if execution_contract else {}),
    }
    _atomic_json(run_dir / "context.json", context)
    return context


def _begin_core(args: argparse.Namespace) -> dict[str, Any]:
    """Enter a custom core only when the native Stage Handler selected one.

    Native Diagnose/Plan/Optimize/Hardening commands call this before their
    default core. A normal Step keeps its existing protocol and therefore
    returns selected=false without reporting a failure or changing Step state.
    """
    payload = _step_input(args)
    from .stage_test import prepare_optimize_test
    try:
        prepare_optimize_test(args, payload)
    except Exception as exc:
        # Fixture preparation precedes the native watchdog; report its failures
        # here so a failed integration test cannot remain dispatched forever.
        args.message = f"Stage test input preparation failed: {exc}"
        _fail(args)
        raise
    if payload.get("protocolVersion") != "clawevolve.stage-runtime/v1":
        if "businessInput" in payload:
            business_input = _stage_business_input(payload["businessInput"], args.task_id)
            return {"selected": False, "businessInput": _materialize_loop_feedback(business_input, _run_dir(args))}
        return {"selected": False}
    return {"selected": True, **_begin(args, payload)}


def _validate_result(value: dict[str, Any]) -> None:
    if value.get("hitl") is False:
        if not set(value).issubset({"hitl", "result", "loop"}):
            raise RuntimeFailure("completed result contains unsupported fields")
        if not isinstance(value.get("result"), dict):
            raise RuntimeFailure("hitl=false requires object result")
        if "loop" in value:
            loop = value.get("loop")
            accepts = loop.get("accepts") if isinstance(loop, dict) else None
            files = accepts.get("files") if isinstance(accepts, dict) else None
            if (not isinstance(loop, dict) or loop.get("action") != "request_feedback"
                    or not isinstance(loop.get("prompt"), str) or not loop["prompt"].strip()
                    or len(loop["prompt"]) > 4000
                    or not isinstance(accepts, dict) or type(accepts.get("text")) is not bool
                    or not isinstance(files, list) or len(files) > 20
                    or any(not isinstance(item, str)
                           or not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", item) for item in files)
                    or (not accepts["text"] and not files)):
                raise RuntimeFailure("loop request is invalid")
        return
    if value.get("hitl") is True:
        if set(value) != {"hitl", "question"}:
            raise RuntimeFailure("waiting result must contain only hitl and question")
        question = value.get("question")
        if not isinstance(question, dict):
            raise RuntimeFailure("hitl=true requires question")
        if question.get("format") == "form":
            if not set(question).issubset({"format", "title", "description", "contents", "questions"}):
                raise RuntimeFailure("structured question contains unsupported fields")
            title = question.get("title")
            contents = question.get("contents", [])
            questions = question.get("questions")
            if (not isinstance(title, str) or not title.strip()
                    or not isinstance(contents, list) or len(contents) > 100
                    or not isinstance(questions, list) or len(questions) > 100
                    or (not contents and not questions)):
                raise RuntimeFailure("structured question title/contents/questions are invalid")
            if len(json.dumps(question, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
                raise RuntimeFailure("structured question is too large")
            # ClawWeb owns the full item schema and answer validation. The
            # runtime only enforces the envelope and transport-size boundary.
            return
        tag = question.get("tag")
        if (not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", tag)
                or question.get("format") not in ("text", "html")):
            raise RuntimeFailure("question tag/format is invalid")
        content = question.get("content")
        if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 256 * 1024:
            raise RuntimeFailure("question content is empty or too large")
        return
    raise RuntimeFailure("result must contain boolean hitl")


def _normalize_result(value: dict[str, Any]) -> dict[str, Any]:
    # An explicit legacy discriminator must be validated, never reinterpreted
    # as a business field. Reserved envelope keys cannot mix with bare output.
    if "hitl" in value:
        result = value
    elif "question" in value:
        if set(value) != {"question"}:
            raise RuntimeFailure("question cannot be mixed with a business result")
        result = {"hitl": True, "question": value["question"]}
    else:
        if not value or "result" in value:
            raise RuntimeFailure("expected a business object, question, or explicit legacy envelope")
        result = {"hitl": False, "result": value}
    _validate_result(result)
    # Full Stage business schema validation still belongs to ClawWeb.
    return result


def _fail(args: argparse.Namespace) -> dict[str, Any]:
    message = str(args.message or "Stage Skill 执行失败").strip()[:1000]
    return _report(
        args,
        "failed",
        "Stage Skill 执行失败",
        error={"code": "STAGE_SKILL_EXECUTION_FAILED", "message": message, "retryable": False},
    )
