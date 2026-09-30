#!/usr/bin/env python3
"""Archive one ClawEvolve task directory and upload it through ClawWeb."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPENCLAW_HOME = Path("/home/admin/.openclaw")
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
SAFE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


def _safe_id(value: str, label: str) -> str:
    if not value or len(value) > 128 or any(char not in SAFE_ID_CHARS for char in value):
        raise ValueError(f"invalid {label}")
    return value


def _request_json(url: str, method: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    result = json.loads(body or "{}")
    if not isinstance(result, dict):
        raise RuntimeError("ClawWeb returned a non-object response")
    return result


def _put_file(url: str, path: Path, headers: dict[str, str]) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("invalid upload URL")
    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_cls(parsed.hostname, parsed.port, timeout=120)
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection.putrequest("PUT", target)
    effective_headers = {str(key): str(value) for key, value in headers.items()}
    effective_headers.setdefault("Content-Type", "application/gzip")
    effective_headers["Content-Length"] = str(path.stat().st_size)
    for key, value in effective_headers.items():
        connection.putheader(key, value)
    connection.endheaders()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            connection.send(chunk)
    response = connection.getresponse()
    detail = response.read(4096).decode("utf-8", errors="replace")
    connection.close()
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"artifact upload failed: HTTP {response.status}: {detail}")


def _walk_snapshot(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    entries: list[Path] = []
    skipped: list[dict[str, str]] = []

    def visit(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            skipped.append({"path": str(path.relative_to(root)), "reason": f"lstat_failed: {error}"})
            return
        if stat.S_ISLNK(mode) or stat.S_ISREG(mode):
            entries.append(path)
            return
        if stat.S_ISDIR(mode):
            entries.append(path)
            try:
                children = sorted(path.iterdir(), key=lambda item: item.name)
            except OSError as error:
                skipped.append({"path": str(path.relative_to(root)), "reason": f"list_failed: {error}"})
                return
            for child in children:
                visit(child)
            return
        skipped.append({"path": str(path.relative_to(root)), "reason": "special_file"})

    visit(root)
    return entries, skipped


def build_archive(source: Path, output: Path, task_id: str, archive_id: str) -> dict[str, Any]:
    entries, skipped = _walk_snapshot(source)
    added = 0
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT, dereference=False) as archive:
        for entry in entries:
            relative = entry.relative_to(source)
            arcname = Path(task_id) if relative == Path(".") else Path(task_id) / relative
            try:
                archive.add(entry, arcname=str(arcname), recursive=False)
                added += 1
            except (OSError, tarfile.TarError) as error:
                skipped.append({"path": str(relative), "reason": f"archive_failed: {error}"})
        manifest = {
            "schemaVersion": "clawevolve.task-log-archive.v1",
            "taskId": task_id,
            "archiveId": archive_id,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceRoot": f"clawevolve_results/{task_id}",
            "fileCount": added,
            "skippedEntries": skipped,
            "symlinkPolicy": "preserve-no-follow",
        }
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        import io
        archive.addfile(info, io.BytesIO(data))
    size = output.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise RuntimeError(f"archive exceeds {MAX_ARCHIVE_BYTES} bytes")
    digest = hashlib.sha256()
    with output.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return {"size": size, "sha256": digest.hexdigest(), "entryCount": added, "skippedCount": len(skipped)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--archive-id", required=True)
    parser.add_argument("--clawweb-url", required=True)
    args = parser.parse_args()
    task_id = _safe_id(args.task_id, "task-id")
    archive_id = _safe_id(args.archive_id, "archive-id")
    base_url = args.clawweb_url.rstrip("/")
    source = OPENCLAW_HOME / "workspace" / "clawevolve_results" / task_id
    report_url = f"{base_url}/api/evolve/internal/tasks/{task_id}/log-archives/{archive_id}/report"
    temp_dir = Path(tempfile.mkdtemp(prefix=f"clawevolve-task-log-{archive_id}."))
    try:
        _request_json(report_url, "POST", {"status": "running"})
        if not source.is_dir():
            raise FileNotFoundError(f"task result directory not found: {source}")
        archive_path = temp_dir / f"{archive_id}.tar.gz"
        metadata = build_archive(source, archive_path, task_id, archive_id)
        upload = _request_json(
            f"{base_url}/api/evolve/internal/tasks/{task_id}/log-archives/{archive_id}/upload-url",
            "POST",
            {"size": metadata["size"], "sha256": metadata["sha256"], "contentType": "application/gzip"},
        )
        _put_file(str(upload["url"]), archive_path, dict(upload.get("headers") or {}))
        artifact = dict(upload.get("artifact") or {})
        _request_json(report_url, "POST", {"status": "succeeded", "artifact": artifact, "metadata": metadata})
        print(json.dumps({"ok": True, "task_id": task_id, "archive_id": archive_id, **metadata}))
        return 0
    except Exception as error:  # noqa: BLE001 - top-level job boundary
        error_code = "TASK_RESULT_DIR_NOT_FOUND" if isinstance(error, FileNotFoundError) else "TASK_LOG_ARCHIVE_FAILED"
        try:
            _request_json(report_url, "POST", {
                "status": "failed",
                "error": {"code": error_code, "message": str(error)[:4000]},
            })
        except Exception:
            pass
        print(json.dumps({"ok": False, "task_id": task_id, "archive_id": archive_id, "error": str(error)}))
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
