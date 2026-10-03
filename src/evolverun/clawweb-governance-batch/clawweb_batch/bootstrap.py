"""CLI composition root. Production adapters and secrets are assembled only here."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .adapters.http import ClawWebHTTP, JsonAnalyst, JsonHTTP
from .adapters.nas import NASReader
from .adapters.odps import PyODPSData
from .artifacts import run_lock, write_json
from .config import load_config, load_deployment, load_llm
from .adapters.mounts import prepare_readonly_mounts, verify_readonly_roots
from .pipeline import run, safe_error


def build_runtime(cfg, *, apply: bool):
    """Assemble production adapters in the only composition root."""
    if apply and not cfg.allow_writes:
        raise PermissionError("apply requires deployment allow_writes=true")
    from pypai.utils import env_utils  # AIStudio identity injection; no exported credentials.
    source = PyODPSData(env_utils.get_odps_instance(), cfg.project)
    http = JsonHTTP(cfg.timeout_seconds)
    center = ClawWebHTTP(cfg.clawweb_url, http, apply and cfg.allow_writes)
    analyst = JsonAnalyst(load_llm(cfg.llm_config_file), http, cfg.analysis_max_tokens)
    verify_readonly_roots(cfg.nas_roots)
    nas = NASReader(cfg.nas_roots)
    if not any(x["available"] for x in nas.available()):
        raise ValueError("NAS unavailable: configured evidence roots are not mounted")
    return source, center, analyst, nas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count-first batch governance and verification")
    parser.add_argument("--config", type=Path, required=True, help="batch JSON config, not legacy realtime YAML")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: run all reads/analysis, no effect-center mutations")
    mode.add_argument("--apply", action="store_true", help="requires allow_writes=true in deployment config")
    parser.add_argument("--top-per-lane", type=int)
    parser.add_argument("--end-date")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--prepare-nas", action="store_true", help="reuse trusted deployment NAS mounts with read-only options")
    args = parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(signum, _frame):
        raise InterruptedError(f"batch interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    logging.getLogger("odps").setLevel(logging.ERROR)
    if args.top_per_lane is not None and not 1 <= args.top_per_lane <= 50:
        parser.error("--top-per-lane must be between 1 and 50")
    cfg = None
    try:
        cfg = load_config(args.config.resolve())
        if args.apply and not cfg.allow_writes:
            raise PermissionError("--apply requires deployment allow_writes=true")
        if args.prepare_nas:
            prepare_readonly_mounts(load_deployment(cfg.llm_config_file).get("nas", {}), cfg.nas_roots)
        source, center, analyst, nas = build_runtime(cfg, apply=args.apply)
        now = datetime.now(timezone(timedelta(hours=8)))
        with run_lock(cfg.state_dir):
            report = run(cfg, source, center, analyst, nas, now=now, apply=args.apply,
                         top=args.top_per_lane, end_date=args.end_date)
        print(json.dumps({"status": report["status"], "mode": report["mode"], "output_dir": report["output_dir"],
                          "create_requests": sum(r["kind"] == "create" for r in report["requests"]),
                          "verification_requests": len(report["verification"]), "external_writes": report["external_writes"],
                          "errors": report["errors"]}, ensure_ascii=False), flush=True)
        return 0 if report["status"] == "SUCCEEDED" else 2
    except Exception as exc:
        error = {"status": "FAILED", "stage": "bootstrap", "error_type": type(exc).__name__, "message": safe_error(exc)}
        if cfg:
            write_json(cfg.output_dir / "last-failure.json", error)
        print(json.dumps(error, ensure_ascii=False), flush=True)
        return 2
