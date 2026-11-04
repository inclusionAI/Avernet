"""Explicit exact-resource recovery for a failed reviewed preprod pilot Run.

This command never invents a product stop API or turns 403/UNKNOWN into
quiescence. It reconciles only ledger-created resources in the selected Run:
a file tombstone requires the exact file API 404, and a Session cleanup fence is
persisted through the same finite reviewed termination contract used by a live
Case. Accessible Session messages are additionally verified through the public
exact-scope delivery route; only previously reviewed implicit bootstrap effects
may remain unobservable because of the Human participant boundary.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .adapters import adapter_for
from .case_scope import CaseScopedAdapter
from .inventory import current_manifest
from .ledger import Ledger
from .profile import ConfigError, browser_headers, fingerprint, identifier, origin, require_resolved
from .remote_effects import remote_effect_admission
from .run_state import read as read_state, require_profile_binding, write as write_state
from .correlation import record_response_id
from .identity_binding import require_pilot_execution_binding
from .transport import NoRedirect, tls_context


class RemoteReconcileError(RuntimeError):
    pass


class RemoteMessageDeliveryError(RemoteReconcileError):
    pass


_DELIVERY_TERMINAL = {
    "completed", "failed", "cancelled", "expired", "rejected_capacity",
    "consumed", "discarded_context",
}


class RemoteMessageDeliveryClient:
    """Bounded public route for one exact Message and Session pair."""

    def __init__(self, profile: dict):
        self.profile = profile
        self.base = origin(profile["entrypoint"]["origin"], profile["environment"]["tier"])
        self.headers = browser_headers(profile)
        for header, reference in profile["auth"].get("header_env", {}).items():
            value = os.environ.get(reference, "")
            if (not value or not value.isascii()
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise RemoteMessageDeliveryError("delivery observation requires a valid credential reference")
            self.headers[header] = value
        self.opener = (build_opener(ProxyHandler({}), HTTPSHandler(context=tls_context()), NoRedirect())
                       if profile["auth"] == {"kind": "owned-local"}
                       else build_opener(HTTPSHandler(context=tls_context()), NoRedirect()))

    def list(self, session_id: str, message_id: str) -> tuple[list[dict], dict]:
        session_id = identifier(session_id, "delivery session_id")
        message_id = identifier(message_id, "delivery message_id")
        path = (f"/openapi/v1/collaboration/messages/{quote(message_id, safe='')}"
                f"/deliveries?session_id={quote(session_id, safe='')}")
        request_id = identifier("e2e-reconcile-" + uuid.uuid4().hex, "request_id")
        record = {"operation": "message.deliveries.read", "method": "GET", "path": path,
                  "submitted_request_id": request_id, "http_status": None,
                  "credentials_sent": True, "authorization_sent": False}
        request = Request(self.base + path, method="GET",
                          headers={**self.headers, "Accept": "application/json",
                                   "X-Request-ID": request_id})
        response = None
        started = time.monotonic()
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            record["http_status"] = response.code
            header_id = response.headers.get("x-request-id", "")
            record["request_id_echoed"] = header_id == request_id
            if not record_response_id(record, header_id, source="HEADER",
                                      sensitive_values=self.headers.values()):
                raise RemoteMessageDeliveryError("delivery route response ID is invalid")
            if response.code != 200:
                raise RemoteMessageDeliveryError(f"delivery route returned HTTP {response.code}")
            raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
            if len(raw) > self.profile["policy"]["max_response_bytes"]:
                raise RemoteMessageDeliveryError("delivery response exceeds its byte budget")
            payload = json.loads(raw)
            if not isinstance(payload, list) or len(payload) > 100:
                raise RemoteMessageDeliveryError("delivery route response is not a bounded list")
            return [dict(item) for item in payload], record
        except RemoteMessageDeliveryError:
            raise
        except (OSError, URLError, UnicodeError, json.JSONDecodeError):
            raise RemoteMessageDeliveryError("delivery route network or decoding failed") from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            if response is not None:
                response.close()


def _error_http_status(error: BaseException) -> int | None:
    records = getattr(error, "request_records", None)
    if isinstance(records, list):
        for item in records:
            if isinstance(item, dict) and type(item.get("http_status")) is int:
                return item["http_status"]
    return getattr(error, "http_status", None)


def _message_ids(data: object) -> list[str]:
    if not isinstance(data, dict):
        raise RemoteReconcileError("Session message response is invalid")
    items = data.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise RemoteReconcileError("Session message response is outside its bounded contract")
    result: list[str] = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("id")) is not str or not item["id"]:
            raise RemoteReconcileError("Session message identity is invalid")
        message_id = item["id"]
        if message_id in result:
            raise RemoteReconcileError("Session message identity is duplicated")
        result.append(message_id)
    return result


def _delivery_projection(session_id: str, message_id: str, rows: list[dict],
                         allowed_bots: set[str]) -> dict:
    if len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        raise RemoteReconcileError("delivery projection is invalid or oversized")
    delivery_ids: set[str] = set()
    statuses: set[str] = set()
    targets: set[str] = set()
    for row in rows:
        for field in ("delivery_id", "message_id", "target_bot_id", "kind", "status", "state_version"):
            if field not in row:
                raise RemoteReconcileError("delivery projection lacks a required field")
        if (row["message_id"] != message_id or row["delivery_id"] in delivery_ids
                or row["target_bot_id"] not in allowed_bots
                or row["kind"] not in {"send", "inject"}):
            raise RemoteReconcileError("delivery row is outside the exact Session/actor scope")
        if type(row["state_version"]) is not int or row["state_version"] < 0:
            raise RemoteReconcileError("delivery state version is invalid")
        delivery_ids.add(row["delivery_id"])
        statuses.add(row["status"])
        targets.add(row["target_bot_id"])
        if row["status"] not in _DELIVERY_TERMINAL:
            raise RemoteReconcileError("delivery has not reached an exact terminal state")
    return {"session_id": session_id, "message_id": message_id,
            "delivery_count": len(rows), "statuses": sorted(statuses),
            "target_bot_ids": sorted(targets), "delivery_ids": sorted(delivery_ids)}


def _live_resources(ledger: Ledger) -> list[dict]:
    plan = ledger.cleanup_plan()
    if plan["status"] != "PLANNED" or plan["unresolved_handles"] or plan["retained_handles"]:
        raise RemoteReconcileError("ledger still has unresolved or retained resources")
    groups = [row for row in plan["resources"] if row["kind"] == "group"]
    sessions = [row for row in plan["resources"] if row["kind"] == "session"]
    files = [row for row in plan["resources"] if row["kind"] == "file"]
    if len(groups) != 1 or not 1 <= len(sessions) <= 2 or len(files) > 2:
        raise RemoteReconcileError("live resources exceed the reviewed pilot shape")
    return plan["resources"]


def _case_for(case_id: str) -> dict:
    for case in current_manifest()["cases"]:
        if case["id"] == case_id:
            return case
    raise RemoteReconcileError("Session belongs to an unknown pilot Case")


def _reconcile_file(ledger: Ledger, adapter, row: dict) -> dict:
    parent = ledger._get(row["parent"])
    if parent["kind"] != "session" or parent["state"] != "live" or not parent["resource_id"]:
        raise RemoteReconcileError("file parent is not the exact live Session")
    adapter.file_verify_deleted(parent["resource_id"], row["resource_id"])
    ledger.mark_deleted(row["handle"])
    return {"kind": "file", "resource_id": row["resource_id"],
            "session_id": parent["resource_id"],
            "evidence": "exact file API not-found"}


def _file_items(data: object) -> list[dict]:
    if not isinstance(data, dict):
        raise RemoteReconcileError("Session file list response is invalid")
    items = data.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise RemoteReconcileError("Session file list is outside its bounded contract")
    result: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or type(item.get("file_id")) is not str or not item["file_id"]:
            raise RemoteReconcileError("Session file identity is invalid")
        if item["file_id"] in seen:
            raise RemoteReconcileError("Session file identity is duplicated")
        seen.add(item["file_id"])
        result.append(item)
    return result


def _validate_implicit_file_projection(session_id: str, file_item: dict,
                                       owner_bots: set[str]) -> str:
    for field in ("file_id", "session_id", "file_name", "size", "owner", "storage_backend", "status"):
        if field not in file_item:
            raise RemoteReconcileError("implicit file projection is incomplete")
    if (file_item["session_id"] != session_id or file_item["status"] != "ready"
            or file_item["storage_backend"] != "baas"
            or not isinstance(file_item["file_name"], str) or not file_item["file_name"]
            or type(file_item["size"]) is not int or file_item["size"] <= 0):
        raise RemoteReconcileError("implicit file projection is outside the reviewed contract")
    owner = file_item["owner"]
    if (not isinstance(owner, dict) or owner.get("actor_kind") != "bot"
            or owner.get("actor_id") not in owner_bots):
        raise RemoteReconcileError("implicit file owner is not a bound test Bot")
    return file_item["file_id"]


def _adopt_implicit_file(ledger: Ledger, session_row: dict, owner_bots: set[str],
                         file_item: dict, message_item: dict) -> dict:
    session_id = session_row["resource_id"]
    file_id = _validate_implicit_file_projection(session_id, file_item, owner_bots)
    content = message_item.get("content")
    if (not isinstance(message_item, dict) or message_item.get("sender") != "system"
            or not isinstance(content, str) or file_id not in content):
        raise RemoteReconcileError("implicit file has no exact Session message association")
    handle = ledger.adopt_created_file(
        session_row["handle"], session_row["case_id"], session_row["attempt"],
        f"e2e-reconcile-implicit-{message_item['id']}", file_id,
    )
    return {"kind": "file", "handle": handle, "resource_id": file_id,
            "session_id": session_id, "evidence": "exact Session file/message/owner projection",
            "origin": "implicit_reviewed"}


def _adopt_boundary_implicit_file(ledger: Ledger, session_row: dict,
                                   owner_bots: set[str], file_item: dict) -> dict:
    session_id = session_row["resource_id"]
    file_id = _validate_implicit_file_projection(session_id, file_item, owner_bots)
    handle = ledger.adopt_created_file(
        session_row["handle"], session_row["case_id"], session_row["attempt"],
        f"e2e-reconcile-implicit-boundary-{file_id}", file_id,
    )
    return {"kind": "file", "handle": handle, "resource_id": file_id,
            "session_id": session_id,
            "evidence": "exact Session file/owner projection at participant boundary",
            "origin": "implicit_reviewed"}


def _reconcile_session(ledger: Ledger, adapter, delivery_client, profile: dict,
                       row: dict, group: dict) -> tuple[dict, list[dict], list[dict]]:
    session_id = row["resource_id"]
    data, _ = adapter.session_read(session_id)
    if not isinstance(data, dict) or data.get("session_id") != session_id or data.get("group_id") != group["resource_id"]:
        raise RemoteReconcileError("Session API identity or parent mismatch")
    details: list[dict] = []
    evidence: list[dict] = []
    adopt: list[dict] = []
    try:
        data, record = adapter.session_messages(session_id)
    except Exception as error:
        status = _error_http_status(error)
        if status != 403:
            raise RemoteReconcileError("Session observation did not prove the reviewed participant boundary") from None
        observation = {"message_count": None, "delivery_count": None,
                      "access": "HTTP_403_PARTICIPANT_BOUNDARY",
                      "implicit_effects": "REVIEWED_ALLOWED"}
        records = getattr(error, "request_records", None)
        if isinstance(records, list) and records:
            evidence.append(records[0])
        owner_bots = {actor["bot_id"] for actor in profile["actors"].values()}
        listed, list_record = adapter.file_list(session_id)
        evidence.append(list_record)
        items = _file_items(listed)
        item_by_id = {item["file_id"]: item for item in items}
        expected = {child["resource_id"] for child in ledger.resource_snapshot()
                    if child["kind"] == "file" and child["parent"] == row["handle"]
                    and child["state"] == "live" and child["resource_id"]}
        extra = set(item_by_id) - expected
        missing = expected - set(item_by_id)
        if missing:
            raise RemoteReconcileError("Session is missing a recorded live file")
        if len(extra) == 1:
            adopt.append(_adopt_boundary_implicit_file(
                ledger, row, owner_bots, item_by_id[next(iter(extra))]))
        elif extra:
            raise RemoteReconcileError("Session contains more than one unrecorded live file")
    else:
        message_ids = _message_ids(data)
        message_items = {item["id"]: item for item in data["items"]}
        owner_bots = {actor["bot_id"] for actor in profile["actors"].values()}
        for message_id in message_ids:
            rows, delivery_record = delivery_client.list(session_id, message_id)
            details.append(_delivery_projection(session_id, message_id, rows, owner_bots))
            evidence.append(delivery_record)
        observation = {"message_count": len(message_ids),
                       "delivery_count": sum(item["delivery_count"] for item in details)}
        listed, list_record = adapter.file_list(session_id)
        evidence.append(list_record)
        items = _file_items(listed)
        item_by_id = {item["file_id"]: item for item in items}
        expected = {child["resource_id"] for child in ledger.resource_snapshot()
                    if child["kind"] == "file" and child["parent"] == row["handle"]
                    and child["state"] == "live" and child["resource_id"]}
        extra = set(item_by_id) - expected
        missing = expected - set(item_by_id)
        if missing:
            raise RemoteReconcileError("Session is missing a recorded live file")
        if len(extra) == 1:
            match = item_by_id[next(iter(extra))]
            candidates = [item for item in message_items.values()
                          if isinstance(item, dict) and item.get("sender") == "system"
                          and isinstance(item.get("content"), str)
                          and match["file_id"] in item["content"]]
            if len(candidates) != 1:
                raise RemoteReconcileError("implicit file has no unique exact message")
            adopt.append(_adopt_implicit_file(ledger, row, owner_bots, match, candidates[0]))
        elif extra:
            raise RemoteReconcileError("Session contains more than one unrecorded live file")

    saved = ledger.termination_state(row["handle"])
    if saved is not None and saved["phase"] == "CLEANUP_READY":
        summary = {"phase": "CLEANUP_READY", "quiescence": "CONFIRMED"}
    else:
        case = _case_for(row["case_id"])
        scope = CaseScopedAdapter(adapter, ledger, fingerprint(profile), case, attempt=row["attempt"],
                                  termination_timeout_seconds=profile["policy"]["timeout_seconds"])
        summary = scope.reconcile_session_cleanup_ready(session_id)
    result = {"kind": "session", "resource_id": session_id,
              "case_id": row["case_id"], "observation": observation,
              "termination_phase": summary["phase"], "quiescence": summary["quiescence"]}
    return result, adopt, evidence


def remote_reconcile(profile: dict, *, state_dir: Path, run_id: str, confirm_run_id: str,
                     allow_read: bool, allow_write: bool,
                     delivery_client_factory=RemoteMessageDeliveryClient,
                     _adapter_factory=adapter_for) -> dict:
    if not allow_read or not allow_write or confirm_run_id != run_id:
        raise ConfigError("remote-reconcile requires read and ledger-write permission plus exact Run confirmation")
    require_pilot_execution_binding(profile)
    require_resolved(profile)
    if (profile["environment"]["tier"] != "preprod"
            or profile["entrypoint"]["kind"] not in {"gateway", "bcs-direct"}
            or remote_effect_admission(profile)["status"] != "ADMITTED"):
        raise ConfigError("remote-reconcile requires a reviewed preprod remote effect policy")
    state = read_state(state_dir / "run.json")
    digest = require_profile_binding(state, profile)
    if state.get("run_id") != run_id or state.get("adapter_kind") not in {"gateway", "bcs-direct"}:
        raise ConfigError("remote-reconcile Run or environment binding mismatch")
    if state.get("status") not in {"FAILED", "BLOCKED", "CANCELLED", "CASE_COMPLETED"}:
        raise ConfigError("remote-reconcile requires a terminal or blocked remote Run")
    ledger_path = Path(state["ledger_path"])
    adapter = _adapter_factory(profile, state_dir=state_dir)
    delivery_client = delivery_client_factory(profile)
    resources: list[dict] = []
    evidence: list[dict] = []
    with Ledger(ledger_path, run_id, digest) as ledger:
        live = _live_resources(ledger)
        group = next((row for row in live if row["kind"] == "group"), None)
        if group is None:
            raise RemoteReconcileError("live owned group is missing")
        for row in [item for item in live if item["kind"] == "file"]:
            if row["request_id"].startswith("e2e-reconcile-implicit-"):
                resources.append({"kind": "file", "resource_id": row["resource_id"],
                                  "evidence": "previously adopted exact Session file projection",
                                  "origin": "implicit_reviewed"})
                continue
            resources.append(_reconcile_file(ledger, adapter, row))
        for row in [item for item in live if item["kind"] == "session"]:
            summary, adopted, records = _reconcile_session(ledger, adapter, delivery_client, profile, row, group)
            resources.extend(adopted)
            resources.append(summary)
            evidence.extend(records)
        snapshots = ledger.termination_states()
        if any(item["phase"] != "CLEANUP_READY" for item in snapshots
               if item["session_id"] in {row["resource_id"] for row in live if row["kind"] == "session"}):
            raise RemoteReconcileError("Session termination fence did not persist")
        result = {"schema_version": 1, "status": "RECONCILED", "run_id": run_id,
                  "profile_digest": digest, "state_dir": str(state_dir),
                  "resources": resources, "requests": evidence,
                  "run_state_before": {"status": state.get("status"), "quiescence": state.get("quiescence")},
                  "run_state_after": {"status": state.get("status"), "quiescence": "CONFIRMED"},
                  "cleanup_next": "use the standard remote cleanup command for the same Run"}
        write_state(state_dir / "remote-reconcile.json", result)
        state["quiescence"] = "CONFIRMED"
        write_state(state_dir / "run.json", state)
    return result
