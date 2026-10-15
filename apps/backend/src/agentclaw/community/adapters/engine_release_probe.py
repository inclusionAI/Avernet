"""Fixed Engine release probe over the existing device transport boundary."""

from __future__ import annotations
from typing import Any
from agentclaw.community.core.bot_management.engine_version.models import (
    CurrentVersion,
    Detection,
)
from agentclaw.community.core.bot_management.engine_version.release import valid_release
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
)


async def probe_engine_release(
    transport: DeviceAdapterTransport, conn_info: dict[str, Any]
) -> tuple[Detection, CurrentVersion]:
    try:
        response = await transport.invoke(
            conn_info,
            "POST",
            "/api/bash/exec",
            body={
                "cmd": "printenv AGENTCLAW_IMAGE_VERSION",
                "cwd": "/home/admin/",
                "timeout": 5,
            },
            timeout=10.0,
        )
        return detect(response)
    except TimeoutError:
        return {"status": "read_failed", "reason": "probe_timeout"}, {}
    except DeviceAdapterEndpointNotFoundError:
        return {"status": "read_failed", "reason": "probe_endpoint_unavailable"}, {}
    except DeviceAdapterHTTPStatusError as exc:
        return {
            "status": "read_failed",
            "reason": "probe_endpoint_unavailable"
            if exc.status_code == 501
            else "probe_http_error",
        }, {}
    except Exception:
        return {"status": "read_failed", "reason": "probe_unavailable"}, {}


def detect(response: object) -> tuple[Detection, CurrentVersion]:
    if not isinstance(response, dict) or response.get("success") is not True:
        return {"status": "read_failed", "reason": "probe_invalid_response"}, {}
    data = response.get("data")
    if not isinstance(data, dict):
        return {"status": "read_failed", "reason": "probe_invalid_response"}, {}
    stdout, stderr, code = data.get("stdout"), data.get("stderr"), data.get("exit_code")
    if (
        not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or type(code) is not int
    ):
        return {"status": "read_failed", "reason": "probe_invalid_response"}, {}
    if stderr or code not in {0, 1} or (code == 1 and stdout):
        return {"status": "read_failed", "reason": "probe_command_failed"}, {}
    # Remove one protocol line ending, never whitespace or repeated newlines.
    value = stdout.removesuffix("\n")
    if stdout.endswith("\r\n"):
        value = value.removesuffix("\r")
    if not value or value == "0.1.153":
        current: CurrentVersion = (
            {"raw_version": value, "source": "engine_env"} if value else {}
        )
        return {
            "status": "legacy",
            "reason": "legacy_default" if value else "version_missing",
        }, current
    if not valid_release(value):
        return {"status": "read_failed", "reason": "probe_invalid_output"}, {}
    return {"status": "known"}, {"release_id": value, "source": "engine_env"}
