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


class ArcaRecycleTransport:
    """Provider primitives injected by composition, like ArcaRestartTransport.

    Missing capabilities fail closed; this object never selects another provider.
    """

    def __init__(self, *, destroy=None, is_destroyed=None):
        self.destroy, self.is_destroyed = destroy, is_destroyed

    def submit(self, target, request_id):
        if self.destroy is None or self.destroy(target["sandbox_id"]) is not True:
            raise RuntimeError("Arca recycle release unconfirmed")
        return {"request_id": request_id}

    def observe(self, target):
        if self.is_destroyed is None:
            return "UNKNOWN"
        return (
            "RELEASED" if self.is_destroyed(target["sandbox_id"]) is True else "UNKNOWN"
        )


def submit_release(dependencies, arca, *, target, request_id, owner_id):
    if target["provider"] == "arca":
        return arca().submit(target, request_id)
    if target["provider"] in {"baas", "local"}:
        return submit_baas_release(
            dependencies.baas_runtime(),
            target=target,
            request_id=request_id,
            owner_id=owner_id,
        )
    raise RuntimeError("Recycle provider unavailable")


def observe_release(dependencies, arca, *, target, release):
    if target["provider"] == "arca":
        return arca().observe(target)
    if target["provider"] in {"baas", "local"}:
        return observe_baas_release(
            dependencies.baas_runtime(), target=target, release=release
        )
    return "UNKNOWN"
