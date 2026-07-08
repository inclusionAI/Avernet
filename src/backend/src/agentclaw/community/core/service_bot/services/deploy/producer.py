"""``DeployArtifactProducer`` base + provider-keyed selector.

The producer encapsulates *how* a deployable artifact is produced for one
``(bot, version)`` at the publish **build** phase. Concrete strategies:

* ``ArcaSnapshotProducer`` — wraps the existing ``bot_build_service.build()``
  (rsync + mcporter snapshot). Behavior unchanged.
* ``ExternalComposeProducer`` (+ ``TeclawComposeProducer``) — composes from
  DB+NAS, materializes a content snapshot, freezes ``engine_ext``.

The selector mirrors ``DeviceServiceRouter``: the DI composition root assembles
the ``device_provider -> producer`` map; the router does pure dispatch with a
default fallback and reads no environment (Rule 14: config-driven assembly).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeployArtifact:
    """Result of producing a deployable artifact for one ``(bot, version)``.

    ``ext`` is the subset of fields to merge onto ``BotPublishRecord.ext`` — ARCA's
    mount chain (``migration_path`` / ``build_target_path``) or the external
    producer's ``config_artifact``. Keeping the deployable pointers on ``ext`` is
    what lets the existing verify/online deploy path (which reads ``ext``) stay
    unchanged.
    """

    success: bool
    ext: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class DeployArtifactProducer(abc.ABC):
    """Strategy that produces (and pins) the deployable artifact at build."""

    @abc.abstractmethod
    def produce_artifact(
        self, bot: dict[str, Any], version: int | None
    ) -> DeployArtifact:
        """Produce and pin the deployable artifact. ``version`` is the publish
        snapshot version, or ``None`` for an unversioned (eager-provision) compose."""
        raise NotImplementedError


class DeployArtifactProducerRouter:
    """Select a :class:`DeployArtifactProducer` by ``device_provider``.

    Pure dispatch over a DI-assembled ``providers`` map. Unknown / missing
    provider falls back to ``default_provider_key`` (e.g. ``baas`` in prod,
    ``local`` in dev) — same shape as ``DeviceServiceRouter``.
    """

    def __init__(
        self,
        providers: dict[str, DeployArtifactProducer],
        default_provider_key: str,
    ) -> None:
        if default_provider_key not in providers:
            raise ValueError(
                f"default_provider_key {default_provider_key!r} not in providers "
                f"{list(providers.keys())!r}"
            )
        self._providers: dict[str, DeployArtifactProducer] = dict(providers)
        self._default_key = default_provider_key

    def resolve(self, device_provider: str | None) -> DeployArtifactProducer:
        """Return the producer for ``device_provider``, or the default."""
        if device_provider and device_provider in self._providers:
            return self._providers[device_provider]
        return self._providers[self._default_key]
