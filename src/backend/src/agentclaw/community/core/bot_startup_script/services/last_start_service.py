"""Read the outcome of a bot's last container start (issue #926).

Deliberately a **separate** service from ``BotStartupScriptService``.
``BaasService`` depends on that one (it reads the script while composing a start
command), so giving it a ``BaasService`` of its own would close a dependency
cycle. This one only reads, and nothing in the start path depends on it.

What it reports covers the **whole start sequence**, not the script alone. That
is the accepted cost of appending the script to the platform's own hook: one
command means one exit status, so the platform's boot and the caller's script
share a result. The endpoint is named ``last-start`` for that reason.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from injector import inject

from agentclaw.community.log import get_logger


logger = get_logger()

StartStatus = Literal["success", "failed", "pending"]


@dataclass(frozen=True)
class StartInstanceResult:
    """One instance's outcome for the last start the platform ran."""

    instance_id: str
    status: StartStatus
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool


def _parse_result_message(raw: Any) -> tuple[int | None, str, str, bool]:
    """Unpack what ``serialize_hook_result`` packed into ``result_message``.

    BaaS writes JSON there; older records are plain text. A plain-text record
    is surfaced as stderr rather than dropped — it is usually the error someone
    is looking for.
    """
    if not isinstance(raw, str) or not raw:
        return None, "", "", False
    if not raw.lstrip().startswith("{"):
        return None, "", raw, False
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "", raw, False
    stdout = parsed.get("stdout") or ""
    stderr = parsed.get("stderr") or ""
    truncated = "[truncated]" in stdout or "[truncated]" in stderr
    return parsed.get("exit_code"), stdout, stderr, truncated


def _status_of(device: dict[str, Any], exit_code: int | None) -> StartStatus:
    raw = str(device.get("result_status") or "").upper()
    if raw == "SUCCESS":
        return "success"
    if raw == "FAILED":
        return "failed"
    if exit_code is None:
        return "pending"
    return "success" if exit_code == 0 else "failed"


class BotStartupScriptRunReader:
    """Report the last start's outcome, one entry per instance."""

    @inject
    def __init__(self, baas_service: Any) -> None:
        self._baas_service = baas_service

    def last_start(self, *, publish_id: int | None) -> list[StartInstanceResult]:
        """Return one result per device for ``publish_id``.

        Empty when the bot has never started, when its publish record has aged
        out, or when BaaS cannot be reached — a read of "what happened last
        time" must not fail the request.
        """
        if not publish_id:
            return []
        try:
            progress = self._baas_service.get_publish_progress(
                publish_id=int(publish_id), include_devices=True
            )
        except Exception as exc:  # noqa: BLE001 - a read must not 500
            logger.warning(
                "[last_start] publish progress unavailable: publish_id=%s, error=%s",
                publish_id,
                exc,
            )
            return []

        devices = (progress or {}).get("devices") or []
        results: list[StartInstanceResult] = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            exit_code, stdout, stderr, truncated = _parse_result_message(
                device.get("result_message")
            )
            results.append(
                StartInstanceResult(
                    instance_id=str(device.get("device_uuid") or ""),
                    status=_status_of(device, exit_code),
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    truncated=truncated,
                )
            )
        return results
