"""Shared helpers for async device hook E2E tests.

Provides polling, callback dispatch, approval, and assertion utilities
used across per-publish-type test files.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from .conftest import (
    APITestHelper,
    call_device_callback,
    create_test_bot,
)

log: Any = logging.getLogger("e2e.hook_helpers")

# ── ANSI color tags for log messages ─────────────────────────────────────────

_C = {
    "TIMING": "\033[36m",
    "APPROVE": "\033[32m",
    "WAIT_STATUS": "\033[33m",
    "SEND_CALLBACK": "\033[35m",
    "CALLBACK": "\033[35m",
    "APPROVE_AND_COMPLETE": "\033[34m",
    "ACTIVATE_BOT": "\033[34m",
    "FORCE_SUCCESS": "\033[31m",
}
_R = "\033[0m"
_TAG_RE = __import__("re").compile(r"\[(\w+)]")


class _ColorLogger:
    """Logger wrapper that colorizes [TAG] sections in messages."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _colorize(self, msg: str) -> str:
        def _color(m):
            tag = m.group(1)
            c = _C.get(tag, "\033[1m")
            return f"{c}[{tag}]{_R}"

        return _TAG_RE.sub(_color, str(msg))

    def info(self, msg, *args, **kwargs):
        self._logger.info(self._colorize(msg), *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._logger.warning(self._colorize(msg), *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._logger.error(self._colorize(msg), *args, **kwargs)


log = _ColorLogger(logging.getLogger("e2e.hook_helpers"))


HOOK_SCRIPT = "/bin/echo 'hook executed'"
HOOK_DEPLOY_CONFIG = {
    "after_create_cmd_hook": HOOK_SCRIPT,
    "before_destroy_cmd_hook": HOOK_SCRIPT,
}


# ── Polling helpers ──────────────────────────────────────────────────────────


async def get_devices_from_progress(api: APITestHelper, publish_id: int) -> list[dict]:
    """Get device details from publish progress endpoint."""
    resp = await api.client.get(
        api.publish_url(publish_id, "progress"),
        params=api.params(include_devices="true"),
    )
    assert resp.status_code == 200
    progress = resp.json()["data"]
    devices = []
    for batch_detail in progress.get("device_details", []):
        devices.extend(batch_detail.get("devices", []))
    return devices


async def get_running_batch_devices(api: APITestHelper, publish_id: int) -> list[dict]:
    """Get devices only from currently RUNNING batches (current stage)."""
    resp = await api.client.get(
        api.publish_url(publish_id, "progress"),
        params=api.params(include_devices="true"),
    )
    assert resp.status_code == 200
    progress = resp.json()["data"]
    devices = []
    for batch_detail in progress.get("device_details", []):
        # Only process devices from the current running batch
        if batch_detail.get("status") in ("RUNNING", "PENDING"):
            devices.extend(batch_detail.get("devices", []))
    return devices


async def wait_for_publish_status(
    api: APITestHelper,
    publish_id: int,
    target_statuses: set[str],
    timeout_seconds: float = 0.1,
    poll_interval: float = 0.1,
) -> str:
    """Poll until publish reaches one of the target statuses."""
    t0 = time.monotonic()
    status = "UNKNOWN"
    first_status = True
    while time.monotonic() - t0 < timeout_seconds:
        resp = await api.client.get(
            api.publish_url(publish_id),
            params=api.params(),
        )
        if resp.status_code == 404:
            if "SUCCESS" in target_statuses:
                log.info(
                    f"[WAIT_STATUS] publish={publish_id} → NOT_FOUND (took {time.monotonic() - t0:.2f}s)"
                )
                return "SUCCESS"
            return "NOT_FOUND"
        assert resp.status_code == 200
        status = resp.json()["data"]["status"]
        if first_status:
            log.info(
                f"[WAIT_STATUS] publish={publish_id} initial={status}, targets={target_statuses}"
            )
            first_status = False
        if status in target_statuses:
            log.info(
                f"[WAIT_STATUS] publish={publish_id} reached {status} (took {time.monotonic() - t0:.2f}s)"
            )
            return status
        await asyncio.sleep(poll_interval)
    log.warning(
        f"[WAIT_STATUS] publish={publish_id} timed out after {time.monotonic() - t0:.1f}s, last={status}"
    )
    return status


async def get_publish_status(api: APITestHelper, publish_id: int) -> str:
    """Get current publish status."""
    resp = await api.client.get(
        api.publish_url(publish_id),
        params=api.params(),
    )
    if resp.status_code == 404:
        return "NOT_FOUND"
    assert resp.status_code == 200
    return resp.json()["data"]["status"]


# ── Callback helpers ─────────────────────────────────────────────────────────


async def send_callbacks_for_hook_devices(
    api: APITestHelper,
    publish_id: int,
    result_status: str = "SUCCESS",
    exit_code: int = 0,
    stdout: str = "mock hook output",
    stderr: str = "",
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.1,
) -> list[dict]:
    """Find CREATED devices in the current running batch and send callback for each."""
    t0 = time.monotonic()
    elapsed = 0.0
    created_devices = []
    poll_count = 0
    while elapsed < timeout_seconds:
        devices = await get_running_batch_devices(api, publish_id)
        created_devices = [
            d
            for d in devices
            if d.get("device_uuid") and d.get("result_status") == "PROCESSING"
        ]
        poll_count += 1
        if created_devices:
            break
        if poll_count <= 3 or poll_count % 10 == 0:
            batch_status = {d.get("result_status", "?") for d in devices}
            log.info(
                f"[SEND_CALLBACK] publish={publish_id} poll#{poll_count}: "
                f"no CREATED devices yet, device_statuses={batch_status}, "
                f"elapsed={elapsed:.1f}s"
            )
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    if not created_devices:
        # Final diagnostic: dump all batch/device info
        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )
        progress = resp.json()["data"] if resp.status_code == 200 else {}
        log.error(
            f"[SEND_CALLBACK] publish={publish_id} FAILED: "
            f"no CREATED devices after {timeout_seconds}s ({poll_count} polls). "
            f"progress_status={progress.get('status')}, "
            f"device_details={json.dumps(progress.get('device_details', []), default=str)[:500]}"
        )

    assert created_devices, (
        f"No CREATED devices with device_uuid found for publish_id={publish_id} "
        f"after {timeout_seconds}s — callback was never invoked"
    )

    log.info(
        f"[SEND_CALLBACK] publish={publish_id} found {len(created_devices)} CREATED device(s) "
        f"after {time.monotonic() - t0:.2f}s ({poll_count} polls)"
    )

    for device in created_devices:
        await call_device_callback(
            api,
            device_uuid=device["device_uuid"],
            publish_id=publish_id,
            event_type="start",
            result_status=result_status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    return created_devices


async def try_send_callbacks_for_hook_devices(
    api: APITestHelper,
    publish_id: int,
    result_status: str = "SUCCESS",
    exit_code: int = 0,
    stdout: str = "mock hook output",
    stderr: str = "",
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.1,
) -> list[dict] | None:
    """Try to find and callback CREATED devices. Returns None if none found (no assert)."""
    t0 = time.monotonic()
    created_devices = []
    poll_count = 0
    while time.monotonic() - t0 < timeout_seconds:
        devices = await get_running_batch_devices(api, publish_id)
        created_devices = [
            d
            for d in devices
            if d.get("device_uuid") and d.get("result_status") == "PROCESSING"
        ]
        poll_count += 1
        if created_devices:
            break
        # If publish already completed, no point waiting
        status = await get_publish_status(api, publish_id)
        if status in ("SUCCESS", "FAILED"):
            log.info(
                f"[CALLBACK] publish={publish_id} already {status}, "
                f"skipping callback search"
            )
            break
        await asyncio.sleep(poll_interval)

    if not created_devices:
        log.info(
            f"[CALLBACK] publish={publish_id} no CREATED devices "
            f"after {time.monotonic() - t0:.2f}s ({poll_count} polls)"
        )
        return None

    log.info(
        f"[CALLBACK] publish={publish_id} found {len(created_devices)} devices "
        f"after {time.monotonic() - t0:.2f}s ({poll_count} polls)"
    )

    t_cb = time.monotonic()
    for device in created_devices:
        await call_device_callback(
            api,
            device_uuid=device["device_uuid"],
            publish_id=publish_id,
            event_type="start",
            result_status=result_status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    log.info(
        f"[CALLBACK] publish={publish_id} sent {len(created_devices)} callbacks "
        f"in {time.monotonic() - t_cb:.2f}s"
    )

    return created_devices


async def send_mixed_callbacks(
    api: APITestHelper,
    publish_id: int,
    fail_index: int = -1,
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.1,
) -> list[dict]:
    """Send SUCCESS to all current-stage devices except device at fail_index (gets FAILED)."""
    t0 = time.monotonic()
    elapsed = 0.0
    created_devices = []
    poll_count = 0
    while elapsed < timeout_seconds:
        devices = await get_running_batch_devices(api, publish_id)
        created_devices = [
            d
            for d in devices
            if d.get("device_uuid") and d.get("result_status") == "PROCESSING"
        ]
        poll_count += 1
        if created_devices:
            break
        if poll_count <= 3 or poll_count % 5 == 0:
            batch_status = {d.get("result_status", "?") for d in devices}
            log.info(
                f"[MIXED_CALLBACK] publish={publish_id} poll#{poll_count}: "
                f"no CREATED devices yet, device_statuses={batch_status}, "
                f"elapsed={elapsed:.1f}s"
            )
        await asyncio.sleep(poll_interval)
        elapsed = time.monotonic() - t0

    if not created_devices:
        resp = await api.client.get(
            api.publish_url(publish_id, "progress"),
            params=api.params(include_devices="true"),
        )
        progress = resp.json()["data"] if resp.status_code == 200 else {}
        log.error(
            f"[MIXED_CALLBACK] publish={publish_id} FAILED: "
            f"no CREATED devices after {timeout_seconds}s ({poll_count} polls). "
            f"progress_status={progress.get('status')}, "
            f"device_details={json.dumps(progress.get('device_details', []), default=str)[:500]}"
        )

    assert created_devices, (
        f"No CREATED devices with device_uuid found for publish_id={publish_id} "
        f"after {timeout_seconds}s — callback was never invoked"
    )

    for i, device in enumerate(created_devices):
        if i == fail_index:
            await call_device_callback(
                api,
                device_uuid=device["device_uuid"],
                publish_id=publish_id,
                event_type="start",
                result_status="FAILED",
                exit_code=1,
                stdout="",
                stderr="partial failure",
            )
        else:
            await call_device_callback(
                api,
                device_uuid=device["device_uuid"],
                publish_id=publish_id,
                event_type="start",
                result_status="SUCCESS",
                exit_code=0,
                stdout="ok",
                stderr="",
            )

    return created_devices


# ── Approval helpers ─────────────────────────────────────────────────────────


async def approve_publish(api: APITestHelper, publish_id: int) -> int:
    """Approve a publish. Returns HTTP status code."""
    t0 = time.monotonic()
    resp = await api.client.post(
        api.publish_url(publish_id, "approve"),
        params=api.params(),
        json={"operator": "e2e-test", "request_id": uuid.uuid4().hex},
    )
    log.info(
        f"[APPROVE] publish={publish_id} status_code={resp.status_code} ({time.monotonic() - t0:.2f}s)"
    )
    return resp.status_code


async def dump_publish_diagnostics(
    api: APITestHelper,
    publish_id: int,
    bot_uuid: str | None = None,
    label: str = "DIAGNOSTICS",
) -> dict[str, Any]:
    """Dump full publish state for debugging: status, progress, device details, bot devices.

    Returns the raw progress dict for caller use.
    """
    resp = await api.client.get(api.publish_url(publish_id), params=api.params())
    publish_data = resp.json()["data"] if resp.status_code == 200 else {}
    log.warning(
        f"[{label}] publish={publish_id} status={publish_data.get('status')} "
        f"stage={publish_data.get('stage')} type={publish_data.get('publish_type')}"
    )

    resp = await api.client.get(
        api.publish_url(publish_id, "progress"),
        params=api.params(include_devices="true"),
    )
    progress = resp.json()["data"] if resp.status_code == 200 else {}

    overall = progress.get("overall_progress", {})
    log.warning(
        f"[{label}] progress: batches={overall.get('completed_batches')}/{overall.get('total_batches')} "
        f"devices={overall.get('processed_devices')}/{overall.get('total_devices')} "
        f"failed={overall.get('failed_devices')} pct={overall.get('progress_percentage')}%"
    )

    for stage_info in progress.get("stages", []):
        log.warning(
            f"[{label}] stage={stage_info.get('stage')} status={stage_info.get('status')} "
            f"batches={stage_info.get('batches_completed')}/{stage_info.get('batches_total')} "
            f"devices={stage_info.get('devices_processed')}/{stage_info.get('devices_total')} "
            f"failed={stage_info.get('devices_failed')}"
        )

    for batch_detail in progress.get("device_details", []):
        devices_summary = [
            f"{d.get('device_uuid', '?')[:8]}={d.get('result_status', '?')}"
            for d in batch_detail.get("devices", [])
        ]
        log.warning(
            f"[{label}] batch={batch_detail.get('batch_id')} "
            f"stage={batch_detail.get('stage')} status={batch_detail.get('status')} "
            f"devices=[{', '.join(devices_summary)}]"
        )

    # 3. Bot devices (if bot_uuid provided)
    if bot_uuid:
        resp = await api.client.get(
            f"{api.bot_url(bot_uuid)}/detail-by-uuid",
            params=api.params(),
        )
        if resp.status_code == 200:
            items = resp.json()["data"].get("items", [])
            for item in items:
                device_statuses = [
                    f"{d.get('device_uuid', '?')[:8]}={d.get('status', '?')}"
                    for d in item.get("devices", [])
                ]
                log.warning(
                    f"[{label}] bot_device: provider={item.get('provider_type', '?')} "
                    f"devices=[{', '.join(device_statuses)}]"
                )

        resp = await api.client.get(api.bot_url(bot_uuid), params=api.params())
        if resp.status_code == 200:
            bot_data = resp.json()["data"]
            log.warning(f"[{label}] bot={bot_uuid[:8]} status={bot_data.get('status')}")

    return progress


async def approve_and_complete(
    api: APITestHelper,
    publish_id: int,
    max_iterations: int = 15,
    bot_uuid: str | None = None,
) -> str:
    """Approve publish, send SUCCESS callbacks, and return final status.

    Handles multi-stage pipelines with auto-continue stages:
    - Only approves when publish is in PENDING or APPROVING state
    - Always attempts to send callbacks for CREATED devices
    - Auto-continue stages (pause_for_approval=False) create new CREATED
      devices that need callbacks without requiring another approval

    Args:
        api: API test helper
        publish_id: Publish ID to complete
        max_iterations: Max approve+callback iterations
        bot_uuid: Optional bot UUID for extra diagnostics on failure
    """
    t0 = time.monotonic()
    iter_times: list[str] = []
    for iteration in range(max_iterations):
        it_t0 = time.monotonic()

        t_s = time.monotonic()
        status = await get_publish_status(api, publish_id)
        t_get_status = time.monotonic() - t_s

        stage_info = ""
        try:
            resp = await api.client.get(
                api.publish_url(publish_id), params=api.params()
            )
            if resp.status_code == 200:
                pub = resp.json()["data"]
                stage_info = f" stage={pub.get('stage')}"
        except Exception:
            pass

        log.info(
            f"[APPROVE_AND_COMPLETE] publish={publish_id} iter={iteration} status={status}{stage_info} "
            f"(elapsed={time.monotonic() - t0:.1f}s)"
        )
        if status in ("SUCCESS", "FAILED"):
            log.info(
                f"[APPROVE_AND_COMPLETE] publish={publish_id} done: {status} ({time.monotonic() - t0:.2f}s)"
            )
            return status

        t_approve = 0.0
        if status in ("PENDING", "APPROVING"):
            t_a = time.monotonic()
            code = await approve_publish(api, publish_id)
            t_approve = time.monotonic() - t_a
            if code != 200:
                log.warning(f"[APPROVE_AND_COMPLETE] approve failed: {code}")
                return "APPROVE_FAILED"

        t_cb = time.monotonic()
        result = await try_send_callbacks_for_hook_devices(api, publish_id)
        t_callback = time.monotonic() - t_cb
        if result is None:
            log.info(
                f"[APPROVE_AND_COMPLETE] publish={publish_id} no CREATED devices this iteration"
            )
        else:
            log.info(
                f"[APPROVE_AND_COMPLETE] publish={publish_id} sent {len(result)} callbacks"
            )

        t_w = time.monotonic()
        status = await wait_for_publish_status(
            api, publish_id, {"SUCCESS", "FAILED", "APPROVING"}
        )
        t_wait = time.monotonic() - t_w
        if status in ("SUCCESS", "FAILED"):
            log.info(
                f"[APPROVE_AND_COMPLETE] publish={publish_id} done: {status} ({time.monotonic() - t0:.2f}s)"
            )
            return status

        it_total = time.monotonic() - it_t0
        iter_times.append(
            f"iter{iteration}: status={status} total={it_total:.2f}s "
            f"(get_status={t_get_status:.2f}s approve={t_approve:.2f}s "
            f"callback={t_callback:.2f}s wait={t_wait:.2f}s)"
        )

    log.warning(
        f"[APPROVE_AND_COMPLETE] publish={publish_id} max iterations reached, status={status} ({time.monotonic() - t0:.2f}s)"
    )
    log.warning(
        "[APPROVE_AND_COMPLETE] per-iteration breakdown:\n  " + "\n  ".join(iter_times)
    )
    await dump_publish_diagnostics(api, publish_id, bot_uuid=bot_uuid)
    return status


async def activate_bot(api: APITestHelper, bot: dict) -> None:
    """Activate a bot: approve + callback loop until complete."""
    t0 = time.monotonic()
    publish_id = bot.get("publish_id")
    if not publish_id:
        return
    final = await approve_and_complete(api, publish_id)
    log.info(
        f"[TIMING] activate_bot: {time.monotonic() - t0:.2f}s, final_status={final}"
    )


# ── Bot lifecycle helpers ────────────────────────────────────────────────────


async def create_hook_bot(
    api: APITestHelper,
    name: str,
    device_count: int = 1,
    callback_timeout_seconds: int | None = None,
) -> dict:
    """Create a bot with hook deploy config."""
    return await create_test_bot(
        api,
        name,
        deploy_config=HOOK_DEPLOY_CONFIG,
        device_count=device_count,
        callback_timeout_seconds=callback_timeout_seconds,
    )


async def create_and_activate_bot(
    api: APITestHelper,
    name: str,
    device_count: int = 1,
) -> dict:
    """Create a hook bot and activate it (for use in scale/restart/destroy tests)."""
    t0 = time.monotonic()
    bot = await create_hook_bot(api, name, device_count=device_count)
    t_create = time.monotonic() - t0
    log.info(
        f"[TIMING] create_hook_bot: {t_create:.2f}s, publish_id={bot.get('publish_id')}"
    )

    t1 = time.monotonic()
    await activate_bot(api, bot)
    t_activate = time.monotonic() - t1
    log.info(
        f"[TIMING] activate_bot: {t_activate:.2f}s | "
        f"total create_and_activate: {time.monotonic() - t0:.2f}s"
    )
    return bot


# ── Assertion helpers ────────────────────────────────────────────────────────


def assert_result_message_has_hook_data(
    result_message: str | None, expected_exit_code: int = 0
) -> dict:
    """Assert result_message contains JSON with hook execution data."""
    assert result_message is not None, "result_message should be populated"
    data = json.loads(result_message)
    assert "exit_code" in data, f"result_message missing exit_code: {result_message}"
    assert data["exit_code"] == expected_exit_code, (
        f"Expected exit_code={expected_exit_code}, got {data['exit_code']}"
    )
    return data
