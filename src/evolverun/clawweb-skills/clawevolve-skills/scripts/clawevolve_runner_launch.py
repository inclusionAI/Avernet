"""Read a frozen platform launch, then enter the existing Runner argument path."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

_release_platform = Path(__file__).resolve().parent / "platform"
_source_platform = Path(__file__).resolve().parents[1] / "platform"
sys.path.insert(0, str(_release_platform if _release_platform.is_dir() else _source_platform))
from clawevolve_runtime.runner_environment import resolve_runner_environment

MAX_BYTES = 128 * 1024
ID = re.compile(r"[A-Za-z0-9._:-]{1,256}\Z")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("launch download redirects are not allowed")


def validate_launch(content: bytes, digest: str) -> dict:
    if len(content) > MAX_BYTES or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid launch size or digest")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("launch checksum mismatch")
    launch = json.loads(content.decode("utf-8"))
    fields = {"schemaVersion", "taskId", "stepId", "stage", "invocationId", "runtimeMaintenance", "args"}
    if not isinstance(launch, dict) or set(launch) != fields:
        raise ValueError("invalid launch fields")
    if launch["schemaVersion"] != "clawevolve.runner-launch.v1":
        raise ValueError("unsupported launch schema")
    for key in ("taskId", "stepId", "invocationId"):
        if not isinstance(launch[key], str) or not ID.fullmatch(launch[key]):
            raise ValueError("invalid launch identity")
    if not isinstance(launch["stage"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", launch["stage"]):
        raise ValueError("invalid launch stage")
    if type(launch["runtimeMaintenance"]) is not bool:
        raise ValueError("invalid launch maintenance setting")
    args = launch["args"]
    if not isinstance(args, str) or len(args.encode("utf-8")) > 64 * 1024 or any(c in args for c in "\0\r\n"):
        raise ValueError("invalid launch arguments")
    parts = shlex.split(args)
    for flag, key in (("--task-id", "taskId"), ("--step-id", "stepId")):
        values = []
        for i, part in enumerate(parts):
            if part == flag:
                values.append(parts[i + 1] if i + 1 < len(parts) else "")
            elif part.startswith(flag + "="):
                values.append(part[len(flag) + 1:])
        if values != [launch[key]]:
            raise ValueError("launch argument identity mismatch")
    return launch


def read_launch(url: str, digest: str) -> dict:
    parsed = urlsplit(url)
    local = resolve_runner_environment().allow_loopback_download()
    if (len(url) > 8192 or any(c.isspace() or ord(c) < 32 for c in url)
        or parsed.username or parsed.password or parsed.fragment or not parsed.hostname
        or not (parsed.scheme == "https" or (local and parsed.scheme == "http"
                                            and parsed.hostname in {"localhost", "127.0.0.1"}))):
        raise ValueError("invalid launch URL")
    handlers = [NoRedirect()]
    if local and parsed.hostname in {"localhost", "127.0.0.1"}:
        handlers.append(ProxyHandler({}))
    with build_opener(*handlers).open(url, timeout=30) as response:
        content = response.read(MAX_BYTES + 1)
    return validate_launch(content, digest)


def main() -> None:
    try:
        if len(sys.argv) != 3:
            raise ValueError("expected launch URL and digest")
        launch = read_launch(sys.argv[1], sys.argv[2])
    except Exception:
        # Signed URLs are capabilities; do not echo download errors containing them.
        print('{"ok":false,"error":"invalid or unavailable frozen runner launch"}', file=sys.stderr)
        raise SystemExit(2)
    env = os.environ | {"CLAWEVOLVE_RUNTIME_MAINTENANCE": str(launch["runtimeMaintenance"]).lower()}
    runner = Path(__file__).resolve().with_name("clawevolve_async_runner.sh")
    args = ["bash", str(runner), "--stage", launch["stage"], "--invocation-id", launch["invocationId"],
            "--args-base64", base64.b64encode(launch["args"].encode("utf-8")).decode("ascii")]
    os.execvpe("bash", args, env)


if __name__ == "__main__":
    main()
