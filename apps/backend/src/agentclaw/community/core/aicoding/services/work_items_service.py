"""Aggregate Agent Coding work items across a user's reachable Bots.

The HTTP adapters own authentication and wire parsing. This service owns the
product invariant: enumerate owner/collaborator Coding Bots, fan out to each
Relay concurrently, normalize session rows, apply fallback filters for old and
new Relay versions, and never let one bad Bot fail the whole snapshot.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import httpx
from injector import inject

from agentclaw.community.core.aicoding.work_items_service_protocol import (
    AICodingWorkItemsServiceProtocol,
)
from agentclaw.community.core.aicoding.work_item_bots import (
    WorkItemsBotLister,
    WorkItemsBotTarget,
)
from agentclaw.community.core.aicoding.work_items_models import (
    SessionSource,
    WorkItem,
    WorkItemBotStatus,
    WorkItemsData,
    WorkItemsFilters,
)
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)

_SESSIONS_PATH = "/api/aicoding/sessions"
_WORK_ITEMS_TIMEOUT = 5.0
_LOCAL_ENGINE_URL = "http://127.0.0.1:20003"
_DEFAULT_SCAN_LIMIT = 50


class _ProbeResult:
    def __init__(
        self,
        summary_status: WorkItemBotStatus,
        items: list[WorkItem],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.summary_status = summary_status
        self.items = items
        self.error_code = error_code
        self.error_message = error_message


class AICodingWorkItemsService(AICodingWorkItemsServiceProtocol):
    """Shared fan-out implementation for the internal and OpenAPI adapters."""

    @inject
    def __init__(
        self,
        bot_lister: WorkItemsBotLister,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
    ) -> None:
        self._bot_lister = bot_lister
        self._resolver = resolver
        self._transport = transport

    async def get_work_items(
        self,
        *,
        current_user_id: str,
        source_filter: list[str] | None,
        since_ms: int | None,
        run_status_filter: list[str] | None,
        scan_limit: int | None,
    ) -> WorkItemsData:
        """Return one filtered work-items snapshot for ``current_user_id``."""
        resolved_scan_limit = _DEFAULT_SCAN_LIMIT if scan_limit is None else scan_limit
        targets = self._bot_lister.list_bot_targets(current_user_id)
        relay_params = _relay_params(
            current_user_id=current_user_id,
            scan_limit=resolved_scan_limit,
            source_filter=source_filter,
            since_ms=since_ms,
            run_status_filter=run_status_filter,
        )

        probes = await asyncio.gather(
            *(
                self._probe_bot(target, current_user_id, relay_params)
                for target in targets
            )
        )

        items: list[WorkItem] = []
        for probe in probes:
            items.extend(probe.items)
        items = _filter_work_items(items, source_filter, since_ms, run_status_filter)
        items.sort(
            key=lambda item: (
                -item.updated_at_ms,
                item.bot_id,
                item.owner_id,
                item.session_key,
            )
        )

        counts = Counter((item.bot_id, item.owner_id) for item in items)
        bot_summaries = [
            _bot_summary(target, probe, counts)
            for target, probe in zip(targets, probes, strict=True)
        ]

        return WorkItemsData(
            items=items,
            bots=bot_summaries,
            partial=any(summary.status == "error" for summary in bot_summaries),
            scan_limit=resolved_scan_limit,
            filters=WorkItemsFilters(
                source=source_filter,  # type: ignore[arg-type]
                since_ms=since_ms,
                run_status=run_status_filter,
            ),
        )

    async def _probe_bot(
        self,
        target: WorkItemsBotTarget,
        current_user_id: str,
        params: dict[str, Any],
    ) -> _ProbeResult:
        try:
            ctx = self._resolver.resolve_for_bot(target.bot_id, target.owner_id)
        except DeviceNotBoundError:
            return _ProbeResult("unavailable", [])
        except (UnknownProviderError, ConnInfoBuildError) as exc:
            return _ProbeResult("error", [], "RESOLVE_ERROR", str(exc))

        try:
            if ctx.provider == "local":
                result = await _probe_local(params)
            else:
                result = await self._transport.invoke(
                    ctx.conn_info,
                    "GET",
                    _SESSIONS_PATH,
                    params=params,
                    timeout=_WORK_ITEMS_TIMEOUT,
                )
            data = result.get("data", []) if isinstance(result, dict) else None
            if result.get("success") is not True or not isinstance(data, list):
                message = str(
                    result.get("message")
                    or result.get("error")
                    or "Engine session list failed"
                )
                code, separator, detail = message.partition(":")
                return _ProbeResult(
                    "error",
                    [],
                    code.strip() if separator else "ENGINE_SESSION_LIST_FAILED",
                    detail.strip() if separator else message,
                )
            sessions = [
                _map_work_item(target, current_user_id, session) for session in data
            ]
            return _ProbeResult(
                "ok",
                [session for session in sessions if session is not None],
            )
        except DeviceAdapterEndpointNotFoundError as exc:
            return _ProbeResult("error", [], "ENGINE_HTTP_404", str(exc))
        except DeviceAdapterHTTPStatusError as exc:
            return _ProbeResult("error", [], f"ENGINE_HTTP_{exc.status_code}", str(exc))
        except DeviceAdapterTimeoutError as exc:
            return _ProbeResult("error", [], "ENGINE_TIMEOUT", str(exc))
        except Exception as exc:
            return _ProbeResult("error", [], "ENGINE_SESSION_LIST_FAILED", str(exc))


def _relay_params(
    *,
    current_user_id: str,
    scan_limit: int,
    source_filter: list[str] | None,
    since_ms: int | None,
    run_status_filter: list[str] | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "user_id": current_user_id,
        "session_kind": "user",
        "limit": scan_limit,
        "offset": 0,
    }
    if source_filter is not None:
        params["source"] = ",".join(source_filter)
    if since_ms is not None:
        params["since_ms"] = since_ms
    if run_status_filter is not None:
        params["run_status"] = ",".join(run_status_filter)
    return params


def _bot_summary(
    target: WorkItemsBotTarget,
    probe: _ProbeResult,
    counts: Counter[tuple[str, str]],
) -> Any:
    from agentclaw.community.core.aicoding.work_items_models import (
        WorkItemBotSummary,
    )

    return WorkItemBotSummary(
        bot_id=target.bot_id,
        bot_name=target.bot_name,
        owner_id=target.owner_id,
        count=counts[(target.bot_id, target.owner_id)],
        status=probe.summary_status,
        error_code=probe.error_code,
        error_message=probe.error_message,
    )


async def _probe_local(params: dict[str, Any]) -> dict[str, Any]:
    url = f"{_LOCAL_ENGINE_URL}{_SESSIONS_PATH}"
    try:
        async with httpx.AsyncClient(timeout=_WORK_ITEMS_TIMEOUT) as client:
            response = await client.get(url, params=params)
        if response.status_code != 200:
            return {
                "success": False,
                "message": (
                    f"ENGINE_HTTP_{response.status_code}: engine returned "
                    f"HTTP {response.status_code}"
                ),
            }
        result = response.json()
        if not isinstance(result, dict):
            return {"success": False, "message": "ENGINE_MALFORMED_RESPONSE"}
        return result
    except Exception as exc:
        return {"success": False, "message": f"ENGINE_PROBE_ERROR: {exc}"}


def _map_work_item(
    target: WorkItemsBotTarget,
    current_user_id: str,
    session: dict[str, Any],
) -> WorkItem | None:
    if not isinstance(session, dict):
        return None
    session_key = str(session.get("id") or session.get("sessionKey") or "")
    if not session_key:
        return None
    updated_at = str(
        session.get("gmt_modified")
        or session.get("updatedAt")
        or session.get("updated_at")
        or ""
    )
    return WorkItem(
        bot_id=target.bot_id,
        bot_name=target.bot_name,
        owner_id=target.owner_id,
        session_key=session_key,
        session_user_id=_optional_text(session.get("user_id")),
        title=_optional_text(session.get("title")) or session_key,
        source=_session_source(session_key, current_user_id),
        run_status=str(session.get("run_status") or ""),
        updated_at=updated_at,
        updated_at_ms=_timestamp_ms(updated_at),
        cwd=_optional_text(session.get("cwd")),
        model=_optional_text(session.get("model")),
        ext_info=(
            session.get("ext_info")
            if isinstance(session.get("ext_info"), dict)
            else None
        ),
    )


def _filter_work_items(
    items: list[WorkItem],
    sources: list[str] | None,
    since_ms: int | None,
    run_statuses: list[str] | None,
) -> list[WorkItem]:
    result: dict[tuple[str, str, str], WorkItem] = {}
    for item in items:
        if not item.run_status or item.run_status == "idle":
            continue
        if sources is not None and item.source not in sources:
            continue
        if since_ms is not None and item.updated_at_ms < since_ms:
            continue
        if run_statuses is not None and not _run_status_matches(
            item.run_status, run_statuses
        ):
            continue
        key = (item.bot_id, item.owner_id, item.session_key)
        current = result.get(key)
        if current is None or item.updated_at_ms >= current.updated_at_ms:
            result[key] = item
    return list(result.values())


def _run_status_matches(run_status: str, expected: list[str]) -> bool:
    if "non_idle" in expected:
        return bool(run_status) and run_status != "idle"
    return run_status in expected


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _session_source(session_key: str, current_user_id: str) -> SessionSource:
    if session_key.endswith(":agent:member-task"):
        return "collab"
    if session_key.startswith("bcs-cli:"):
        return "others"

    creator = _session_creator(session_key)
    if creator is not None and not _same_user_id(creator, current_user_id):
        return "others"
    return "mine"


def _session_creator(session_key: str) -> str | None:
    if session_key.startswith("user:"):
        creator = session_key.removeprefix("user:").split(":", 1)[0]
        return creator or None
    parts = session_key.split(":")
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] in {"user", "direct"} and parts[index + 1]:
            return parts[index + 1]
    return None


def _same_user_id(left: str, right: str) -> bool:
    left_value = left.strip().lower()
    right_value = right.strip().lower()
    if not left_value or not right_value:
        return False
    if left_value.isdigit() and right_value.isdigit():
        left_value = left_value.lstrip("0") or "0"
        right_value = right_value.lstrip("0") or "0"
    return left_value == right_value


def _timestamp_ms(raw: str) -> int:
    value = raw.strip()
    if not value:
        return 0
    if value.endswith("Z"):
        numeric = value[:-1]
        if numeric.isdigit():
            return int(numeric)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


__all__ = ["AICodingWorkItemsService"]
