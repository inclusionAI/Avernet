#!/usr/bin/env python3
"""Task-bound immutable Pack/Restore handler."""
import argparse, hashlib, json, os, subprocess, sys, tempfile, urllib.parse, urllib.request, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_artifact_url_client import ArtifactUrlClient
from lib_http_retry import retry_http

BUCKET = os.environ.get("CLAWEVOLVE_ARTIFACT_BUCKET", "clawevolve-artifacts")
PREFIX = os.environ.get("CLAWEVOLVE_ARTIFACT_PREFIX", "evolution")
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/admin/.openclaw/workspace"))
SKILL_BASE = Path(os.environ.get("SKILL_BASE_DIR") or Path(__file__).resolve().parents[3]).expanduser().resolve()
DEFAULT_CLAWWEB_URL = "http://127.0.0.1:5173"

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name): os.unlink(temp_name)

def load_json(path):
    if not path.exists(): return {}
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc: raise RuntimeError(f"invalid JSON state: {path}: {exc}") from exc

def error_tail(value, limit=3000):
    """Keep the actionable tail where subprocesses print their final failure."""
    text = str(value or "")
    if len(text) <= limit: return text
    return f"...<truncated {len(text) - limit} leading chars>\n{text[-limit:]}"

def skill_script(skill, name):
    path = (SKILL_BASE / skill / "scripts" / name).resolve()
    try:
        path.relative_to(SKILL_BASE)
    except ValueError as exc:
        raise RuntimeError(f"unsafe Skill script path: {path}") from exc
    if path.is_file(): return path
    raise RuntimeError(f"{skill}/{name} not found")

def artifact_client(args):
    return ArtifactUrlClient(args.clawweb_url, args.task_id, args.step_id)

def report(args, status, summary, output=None, error=None):
    url = f"{args.clawweb_url}/api/evolve/internal/tasks/{urllib.parse.quote(args.task_id)}/steps/{urllib.parse.quote(args.step_id)}/report"
    payload = {"status": status, "summary": summary}
    if output is not None: payload["output"] = output
    if error is not None: payload["error"] = error
    def request_once():
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=180) as response: return response.read()
    retry_http(request_once, label="ClawWeb Step report")

def manifest_artifact(manifest, kind):
    if kind == "baseline":
        return {"ref": manifest.get("artifactRef"), "size": manifest.get("size"), "sha256": manifest.get("sha256"), "contentType": manifest.get("contentType")}
    if kind == "snapshot": return manifest.get("artifact") or {}
    return (manifest.get("objects") or {}).get("artifact") or {}

def validate_artifact(value, args):
    ref, size, digest = str(value.get("ref") or ""), value.get("size"), str(value.get("sha256") or "")
    bucket = getattr(args, "artifact_bucket", "") or BUCKET
    expected = f"oss://{bucket}/{PREFIX}/{args.source_task_id}/"
    suffix = "baseline/artifact_v0.zip" if args.source_kind == "baseline" else "snapshots/artifact.zip" if args.source_kind == "snapshot" else f"rounds/round-{args.source_round:03d}/artifacts/artifact_v{args.source_round}.zip"
    if ref != expected + suffix or value.get("contentType") != "application/zip": raise RuntimeError("manifest contains invalid Pack reference")
    if not isinstance(size, int) or size < 0 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): raise RuntimeError("manifest contains invalid Pack digest")
    return ref[len(f"oss://{bucket}/"):], size, digest

def frozen_restore_input(args):
    url = f"{args.clawweb_url}/api/evolve/internal/tasks/{urllib.parse.quote(args.task_id)}/steps/{urllib.parse.quote(args.step_id)}/input"
    def request_once():
        with urllib.request.urlopen(url, timeout=60) as response: return json.loads(response.read())
    payload = retry_http(request_once, label="ClawWeb Step input")
    config = (payload.get("task") or {}).get("config") or {}
    if config.get("sourceTaskId") != args.source_task_id or config.get("sourceKind") != args.source_kind:
        raise RuntimeError("Restore command and frozen Step Input disagree")
    frozen_round = int(config.get("sourceRound") or 0)
    if frozen_round != args.source_round: raise RuntimeError("Restore round and frozen Step Input disagree")
    artifact = config.get("artifact") or {}
    validate_artifact(artifact, args)
    return artifact

def run_pack(args, client, out):
    target, state_path = out / "artifact.zip", out / "snapshot-state.json"
    state = load_json(state_path)
    if target.exists():
        if state.get("sha256") != sha256(target) or state.get("size") != target.stat().st_size: raise RuntimeError("existing snapshot is incomplete or changed")
    else:
        if state: raise RuntimeError("snapshot state exists but artifact is missing")
        script = skill_script("clawevolve-pack", "pack.sh")
        proc = subprocess.run(["bash", str(script), "--workspace", str(WORKSPACE), "--out-dir", str(out), "--evolve-run-id", args.task_id], text=True, capture_output=True)
        if proc.returncode: raise RuntimeError(error_tail(proc.stdout + proc.stderr, 4000))
        files = [p for p in out.glob("*.zip") if p != target]
        if not files: raise RuntimeError("pack artifact not found")
        files[-1].replace(target)
        state = {"size": target.stat().st_size, "sha256": sha256(target), "artifactUploaded": False, "manifestUploaded": False, "clawwebReported": False}
        write_json(state_path, state)
    if not state.get("artifactUploaded"):
        artifact = client.upload("snapshot-pack", target, "application/zip")
        state["artifactUploaded"] = True; state["artifact"] = artifact; write_json(state_path, state)
    else:
        artifact = state.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("Pack state is incomplete: uploaded artifact metadata is missing")
    manifest_path = out / "snapshot-manifest.json"; write_json(manifest_path, {"schemaVersion":"clawevolve.snapshot.v1","taskId":args.task_id,"artifact":artifact})
    if not state.get("manifestUploaded"):
        client.upload("snapshot-manifest", manifest_path, "application/json"); state["manifestUploaded"] = True; write_json(state_path, state)
    return {"summary":"Pack 已创建","pack":{"status":"available","artifact":artifact}}, state_path

def run_restore(args, client, out):
    frozen = frozen_restore_input(args)
    deploy_state = {"deployStarted": False, "deploySucceeded": False, "workspaceModified": False}
    args.deploy_state = deploy_state
    with tempfile.TemporaryDirectory(prefix="clawevolve-restore-", dir=str(out)) as temp_dir:
        manifest_path = Path(temp_dir) / "manifest.json"; client.download_restore("manifest", manifest_path)
        manifest = load_json(manifest_path)
        expected_schema = "clawevolve.baseline-artifact.v1" if args.source_kind == "baseline" else "clawevolve.snapshot.v1" if args.source_kind == "snapshot" else "clawevolve.round-artifacts.v1"
        if manifest.get("schemaVersion") != expected_schema: raise RuntimeError("Restore manifest schema mismatch")
        manifest_task = manifest.get("taskId") if args.source_kind != "round" else (manifest.get("identity") or {}).get("taskId")
        if manifest_task != args.source_task_id: raise RuntimeError("Restore manifest task mismatch")
        if args.source_kind == "round" and int((manifest.get("identity") or {}).get("round") or 0) != args.source_round: raise RuntimeError("Restore manifest round mismatch")
        artifact = manifest_artifact(manifest, args.source_kind); _key, expected_size, expected_hash = validate_artifact(artifact, args)
        if any(artifact.get(k) != frozen.get(k) for k in ("ref", "size", "sha256", "contentType")): raise RuntimeError("Manifest Artifact and frozen Step Input disagree")
        temp_zip = Path(temp_dir) / "source.zip.tmp"; artifact_ticket = client.download_restore("artifact", temp_zip)
        if artifact_ticket.get("artifact") != frozen: raise RuntimeError("Artifact download URL and frozen Step Input disagree")
        if temp_zip.stat().st_size != expected_size or sha256(temp_zip) != expected_hash: raise RuntimeError("downloaded Pack size or SHA-256 mismatch")
        if not zipfile.is_zipfile(temp_zip): raise RuntimeError("downloaded Pack is not a valid ZIP")
        source_zip = out / "source.zip"; os.replace(temp_zip, source_zip)
    script = skill_script("clawevolve-deploy", "deploy.sh")
    deploy_state["deployStarted"] = True
    proc = subprocess.run(["bash", str(script), "--image", str(source_zip), "--workspace", str(WORKSPACE), "--skip-evolve-results", "--force-overwrite", "--evolve-run-id", args.task_id], text=True, capture_output=True)
    deploy_log = out / "deploy.log"
    deploy_log.write_text(
        f"exit_code={proc.returncode}\n\n[stdout]\n{proc.stdout}\n\n[stderr]\n{proc.stderr}",
        encoding="utf-8",
    )
    deploy_state["deployLog"] = str(deploy_log)
    if proc.returncode: raise RuntimeError(error_tail(proc.stdout + proc.stderr, 4000))
    deploy_state.update({"deploySucceeded": True, "workspaceModified": True})
    return {"summary":"Pack 已恢复","restore":{"status":"succeeded","sourceTaskId":args.source_task_id,"sourceKind":args.source_kind,"sourceRound":args.source_round,"artifact":artifact, **deploy_state}}, None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--task-id", required=True); ap.add_argument("--step-id", required=True)
    ap.add_argument("--mode", choices=["pack", "restore"], required=True); ap.add_argument("--source-task-id", default="")
    ap.add_argument("--source-kind", choices=["baseline", "snapshot", "round"], default="snapshot"); ap.add_argument("--source-round", type=int, default=0)
    ap.add_argument("--clawweb-url", default=os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL") or DEFAULT_CLAWWEB_URL)
    ap.add_argument("--version", choices=["internalversion", "openversion"], default="internalversion")
    ap.add_argument("--artifact-bucket", default="")
    args = ap.parse_args()
    # COSEC: only the explicit local mode can override the artifact namespace; internal defaults are unchanged.
    if args.artifact_bucket and args.version != "openversion": ap.error("artifact-bucket override requires openversion")
    if args.version == "openversion":
        if not __import__('re').fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", args.artifact_bucket): ap.error("openversion requires a valid artifact-bucket")

    args.clawweb_url = args.clawweb_url.rstrip("/")
    for name, value in (("task-id", args.task_id), ("step-id", args.step_id)):
        if not __import__('re').fullmatch(r"[A-Za-z0-9._:-]{1,128}", value): ap.error(f"invalid {name}")
    if args.source_task_id and not __import__('re').fullmatch(r"[A-Za-z0-9._:-]{1,128}", args.source_task_id): ap.error("invalid source-task-id")
    if args.mode == "restore" and (not args.source_task_id or (args.source_kind == "round" and args.source_round < 1)): ap.error("invalid restore source")
    out = WORKSPACE / "clawevolve_results" / args.task_id / args.mode; out.mkdir(parents=True, exist_ok=True)
    result_path = out / "result.json"
    state_path = out / "snapshot-state.json" if args.mode == "pack" else None
    if result_path.exists():
        output = load_json(result_path)
    else:
        try:
            client = artifact_client(args)
            output, state_path = run_pack(args, client, out) if args.mode == "pack" else run_restore(args, client, out)
            write_json(result_path, output)
        except BaseException as exc:
            phase = "pack" if args.mode == "pack" else "restore"
            deploy_state = getattr(args, "deploy_state", {"deployStarted": False, "deploySucceeded": False, "workspaceModified": False})
            if deploy_state.get("deployStarted") and not deploy_state.get("deploySucceeded"):
                deploy_state["workspaceModified"] = "unknown"
            error = {"code": f"{phase.upper()}_HANDLER_FAILED", "message": error_tail(exc, 1000), "phase": phase, **deploy_state}
            write_json(out / "failure.json", error)
            try: report(args, "failed", f"{phase} 执行失败", error=error)
            except Exception as report_exc: print(f"failed to report terminal state: {report_exc}", file=__import__('sys').stderr)
            raise
    try:
        report(args, "succeeded", output["summary"], output)
        if state_path:
            state = load_json(state_path); state["clawwebReported"] = True; write_json(state_path, state)
        print(json.dumps(output, ensure_ascii=False))
    except Exception as exc:
        write_json(out / "clawweb-report-pending.json", {"status":"pending", "message":str(exc)[:1000]})
        raise

if __name__ == "__main__": main()
