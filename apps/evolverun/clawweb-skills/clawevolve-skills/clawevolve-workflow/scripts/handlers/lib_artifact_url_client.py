"""Constrained ClawWeb Artifact URL client implemented with Python stdlib only."""

from __future__ import annotations

import hashlib
import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_http_retry import retry_http


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_request(url: str, payload: dict) -> dict:
    def request_once() -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise RuntimeError(f"ClawWeb Artifact URL HTTP {exc.code}: {detail}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("ClawWeb Artifact URL returned invalid JSON")
        return value

    value = retry_http(request_once, label="ClawWeb Artifact URL")
    if not isinstance(value, dict):
        raise RuntimeError("ClawWeb Artifact URL returned invalid JSON")
    return value


def _put_file(url: str, path: Path, headers: dict[str, str]) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("Artifact upload URL is invalid")
    target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    request_path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=300)
    print(
        "[artifact-url] upload connect "
        f"scheme={parsed.scheme} host={parsed.hostname} port={target_port} "
        f"size={path.stat().st_size}",
        file=sys.stderr,
        flush=True,
    )
    try:
        connection.putrequest("PUT", request_path)
        connection.putheader("Content-Length", str(path.stat().st_size))
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                connection.send(chunk)
        response = connection.getresponse()
        detail = response.read(2000)
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Artifact upload HTTP {response.status}: {detail.decode(errors='replace')}")
    except Exception as exc:
        raise RuntimeError(
            "Artifact upload connection failed: "
            f"scheme={parsed.scheme} host={parsed.hostname} port={target_port} "
            f"error={type(exc).__name__}: {exc}"
        ) from exc
    finally:
        connection.close()


def _download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.download")
    try:
        with urllib.request.urlopen(url, timeout=300) as response, temp.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


class ArtifactUrlClient:
    def __init__(self, clawweb_url: str, task_id: str, step_id: str):
        base = clawweb_url.rstrip("/")
        task = urllib.parse.quote(task_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        self.base_url = f"{base}/api/evolve/internal/tasks/{task}/steps/{step}/artifacts"

    def upload(self, kind: str, path: Path, content_type: str, round_no: int | None = None) -> dict:
        payload = {
            "kind": kind,
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
            "contentType": content_type,
        }
        if round_no is not None:
            payload["round"] = round_no
        ticket = _json_request(f"{self.base_url}/upload-url", payload)
        if ticket.get("method") != "PUT" or not isinstance(ticket.get("url"), str):
            raise RuntimeError("ClawWeb upload URL response is invalid")
        _put_file(str(ticket["url"]), path, {str(k): str(v) for k, v in (ticket.get("headers") or {}).items()})
        artifact = ticket.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("ClawWeb upload URL response is missing artifact")
        return artifact

    def download_restore(self, kind: str, path: Path) -> dict:
        ticket = _json_request(f"{self.base_url}/restore-download-url", {"kind": kind})
        if ticket.get("method") != "GET" or not isinstance(ticket.get("url"), str):
            raise RuntimeError("ClawWeb download URL response is invalid")
        _download_file(str(ticket["url"]), path)
        return ticket

    def download_accepted(self, source_round: int, path: Path) -> dict:
        ticket = _json_request(f"{self.base_url}/accepted-download-url", {"sourceRound": source_round})
        if ticket.get("method") != "GET" or not isinstance(ticket.get("url"), str):
            raise RuntimeError("ClawWeb accepted Pack download URL response is invalid")
        artifact = ticket.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("ClawWeb accepted Pack response is missing artifact")
        _download_file(str(ticket["url"]), path)
        if path.stat().st_size != artifact.get("size") or file_sha256(path) != artifact.get("sha256"):
            path.unlink(missing_ok=True)
            raise RuntimeError("downloaded accepted Pack size or SHA-256 mismatch")
        return ticket
