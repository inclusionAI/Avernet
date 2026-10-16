"""agentswarm CLI — submit a swarm run, poll it, print the final report.

Drives the same Service API contract the FastAPI router exposes, over
HTTP. Base URL honors ``AGENTSWARM_BASE_URL`` (AvernetConfig-style env
override, default http://127.0.0.1:8000).

Tenant (Stage B, D25): the request body carries no tenant; the tenant is
resolved server-side from the authenticated principal only, and the server
never reads the client ``X-Tenant-Id`` header. The CLI still sends
``--tenant`` / ``AGENTSWARM_TENANT`` as ``X-Tenant-Id`` for
dev/forward-compat, but the server ignores it: ``--tenant`` cannot
resolve a tenant on its own, and a principal without a tenant fails with
HTTP 400 ``{"detail": "tenant unresolvable"}``. Operators must ensure the
authenticated principal carries the tenant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TENANT_HEADER = "X-Tenant-Id"
TENANT_ENV_VAR = "AGENTSWARM_TENANT"
TERMINAL_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "GUARD_KILLED", "STUCK"}
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentswarm",
        description="Drive agentswarm swarm runs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="Submit a run from a JSON goal spec")
    submit.add_argument("--spec", required=True,
                        help="JSON file: goal, agents[, provider, model, idempotency_key]")
    submit.add_argument("--tenant", default=None,
                        help=f"sent as X-Tenant-Id but ignored by the "
                             f"server; only the authenticated principal's "
                             f"tenant is honoured (default: ${TENANT_ENV_VAR})")
    submit.add_argument("--wait", dest="wait", action="store_true", default=False,
                        help="Poll to a terminal state and print the report")
    submit.add_argument("--interval", type=float, default=1.0)
    submit.add_argument("--timeout", type=float, default=600.0)

    get_cmd = sub.add_parser("get", help="Print a run summary as JSON")
    get_cmd.add_argument("--run-id", required=True)
    get_cmd.add_argument("--tenant", default=None,
                         help=f"sent as X-Tenant-Id but ignored by the "
                              f"server; only the authenticated principal's "
                              f"tenant is honoured (default: ${TENANT_ENV_VAR})")

    report_cmd = sub.add_parser("report", help="Print the run's report page")
    report_cmd.add_argument("--run-id", required=True)
    report_cmd.add_argument("--tenant", default=None,
                            help=f"sent as X-Tenant-Id but ignored by the "
                                 f"server; only the authenticated principal's "
                                 f"tenant is honoured (default: ${TENANT_ENV_VAR})")
    return parser


def _tenant_headers(tenant: str | None) -> dict[str, str]:
    return {TENANT_HEADER: tenant} if tenant else {}


def _tenant_arg(args: argparse.Namespace) -> str | None:
    """--tenant flag first, then AGENTSWARM_TENANT; empty string means absent."""
    tenant = getattr(args, "tenant", None) or os.environ.get(TENANT_ENV_VAR)
    tenant = (tenant or "").strip()
    return tenant or None


def _load_spec(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    missing = [k for k in ("goal", "agents") if not spec.get(k)]
    if missing:
        raise SystemExit(f"spec missing required fields: {', '.join(missing)}")
    if spec.get("tenant"):
        print(
            "warning: spec 'tenant' is ignored — the tenant now comes from the "
            f"authenticated principal; pass --tenant or set {TENANT_ENV_VAR} "
            "for the header fallback",
            file=sys.stderr,
        )
        spec.pop("tenant")
    return spec


def _submit(client: httpx.Client, spec: dict[str, Any], tenant: str | None) -> str:
    resp = client.post("/openapi/v1/agentswarm/runs", json=spec, headers=_tenant_headers(tenant))
    resp.raise_for_status()
    return resp.json()["run_id"]


def _get(client: httpx.Client, run_id: str, tenant: str | None) -> dict[str, Any]:
    resp = client.get(f"/openapi/v1/agentswarm/runs/{run_id}", headers=_tenant_headers(tenant))
    resp.raise_for_status()
    return resp.json()


def _report(client: httpx.Client, run_id: str, tenant: str | None) -> str:
    resp = client.get(f"/openapi/v1/agentswarm/runs/{run_id}/report",
                      headers=_tenant_headers(tenant))
    resp.raise_for_status()
    return resp.text


def _poll(client: httpx.Client, run_id: str, tenant: str | None,
          interval: float, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    summary = {"status": "UNKNOWN", "run_id": run_id}
    while time.monotonic() < deadline:
        summary = _get(client, run_id, tenant)
        if summary.get("status") in TERMINAL_STATUSES:
            return summary
        time.sleep(interval)
    return summary


def main(argv: Sequence[str] | None = None, client: httpx.Client | None = None) -> int:
    args = build_parser().parse_args(argv)
    tenant = _tenant_arg(args)
    owns_client = client is None
    if client is None:
        base = os.environ.get("AGENTSWARM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        client = httpx.Client(base_url=base)
    try:
        if args.command == "submit":
            spec = _load_spec(args.spec)
            run_id = _submit(client, spec, tenant)
            print(f"run_id: {run_id}")
            if args.wait:
                summary = _poll(client, run_id, tenant,
                                args.interval, args.timeout)
                print(f"status: {summary.get('status')}")
                if summary.get("status") == "COMPLETED":
                    print(_report(client, run_id, tenant))
            return 0
        if args.command == "get":
            print(json.dumps(_get(client, args.run_id, tenant)))
            return 0
        print(_report(client, args.run_id, tenant))
        return 0
    except httpx.HTTPStatusError as exc:
        print(f"request failed: {exc.response.status_code} {exc.response.text}",
              file=sys.stderr)
        return 1
    finally:
        if owns_client:
            client.close()