"""BaaS-backed providers share strict, observation-only release recovery."""

from __future__ import annotations

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasBotNotFoundError,
)


def submit_baas_release(baas, *, target, request_id, owner_id):
    try:
        result = baas.destroy_bot(
            bot_uuid=target["bot_uuid"],
            operator=owner_id,
            request_id=request_id,
        )
    except BaasBotNotFoundError:
        # A missing logical Bot cannot prove orphan physical devices are gone.
        return {"request_id": request_id}
    return {"publish_id": result.get("publish_id"), "request_id": request_id}


def observe_baas_release(baas, *, target, release):
    publish_id = (release or {}).get("publish_id")
    if publish_id:
        progress = baas.get_publish_progress(int(publish_id), include_devices=False)
        if progress.get("status") != "SUCCESS":
            return "UNKNOWN"
    status = baas.get_recycle_release_status(bot_uuid=target["bot_uuid"])
    devices = baas.list_devices_by_bot_uuid(bot_uuid=target["bot_uuid"])
    if not isinstance(devices, list):
        return "UNKNOWN"
    terminal = all(
        isinstance(d, dict) and d.get("status") in {"RELEASED", "STOPPED"}
        for d in devices
    )
    if terminal and (bool(publish_id) or status == "RELEASED"):
        return "RELEASED"
    return "UNKNOWN"


class BaasRecycleReleaseMixin:
    def submit_recycle_release(self, *, target, request_id, owner_id):
        return submit_baas_release(
            self._baas_service, target=target, request_id=request_id, owner_id=owner_id
        )

    def observe_recycle_release(self, *, target, release):
        return observe_baas_release(self._baas_service, target=target, release=release)
