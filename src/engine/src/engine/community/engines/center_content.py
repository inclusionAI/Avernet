"""Composition root for runtime-owned Center content delivery selection."""

from __future__ import annotations

import threading

from engine.community.config import (
    RepoDelivery,
    current_repo_delivery,
    load_center_content_allowed_hosts,
)
from engine.community.kernel.center_content import CenterContentAdapter
from engine.community.plugins.skills_pool.center_content import (
    DownloadedCenterContentAdapter,
    MountedCenterContentAdapter,
)

_ADAPTERS: dict[tuple[RepoDelivery, tuple[str, ...]], CenterContentAdapter] = {}
_ADAPTERS_LOCK = threading.Lock()


def build_center_content_adapter() -> CenterContentAdapter:
    delivery = current_repo_delivery()
    allowed_hosts = (
        load_center_content_allowed_hosts()
        if delivery is RepoDelivery.DOWNLOAD
        else ()
    )
    key = (delivery, allowed_hosts)
    with _ADAPTERS_LOCK:
        adapter = _ADAPTERS.get(key)
        if adapter is None:
            adapter = (
                DownloadedCenterContentAdapter(allowed_hosts=allowed_hosts)
                if delivery is RepoDelivery.DOWNLOAD
                else MountedCenterContentAdapter()
            )
            _ADAPTERS[key] = adapter
        return adapter


__all__ = ["build_center_content_adapter"]
