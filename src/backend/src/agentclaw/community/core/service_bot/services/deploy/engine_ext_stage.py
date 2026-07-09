"""Stage vocabulary + re-stamp for the artifact's ``engine_ext`` payload.

The external-container publish path enriches ``engine_ext`` with three
backend-owned keys (``bot_id`` / ``owner_id`` / ``stage``). ``bot_id`` and
``owner_id`` are constant for an artifact; ``stage`` is re-stamped as the version
is promoted across environments (draft → canary → release).

This module is the single source of truth for those key names and for the
``PublishStage`` → engine-facing stage-string mapping, and exposes a pure
:func:`restamp_stage` used at two points per promotion: on the artifact dict
**delivered** to the container, and on the **persisted** snapshot.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.service_bot.types import PublishStage

#: Backend-owned ``engine_ext`` key names (injected at build, alongside the
#: engine's opaque payload).
KEY_BOT_ID = "bot_id"
KEY_OWNER_ID = "owner_id"
KEY_STAGE = "stage"

#: ``PublishStage`` → the engine-facing ``engine_ext.stage`` string. The engine
#: expects ``draft`` / ``canary`` / ``release`` — NOT the raw enum values
#: (``verify`` / ``online``). This mapping is the deliberate translation.
STAGE_VALUE: dict[PublishStage, str] = {
    PublishStage.DRAFT: "draft",
    PublishStage.VERIFY: "canary",
    PublishStage.ONLINE: "release",
}


def enrich_engine_ext(
    engine_ext: dict[str, Any],
    *,
    bot_id: Any,
    owner_id: Any,
    stage: PublishStage,
) -> dict[str, Any]:
    """Merge the backend-owned identity/stage keys onto a copy of ``engine_ext``.

    The three keys are written **last** so they are explicit and deterministically
    win on collision with the engine's opaque payload. ``None`` ids normalize to
    ``""``. Used by both the publish producer (stage=draft at build, promoted later)
    and the teclaw device-sync plugin (runtime edits, always stage=draft) so the two
    paths agree on the vocabulary.
    """
    return {
        **engine_ext,
        KEY_BOT_ID: "" if bot_id is None else str(bot_id),
        KEY_OWNER_ID: "" if owner_id is None else owner_id,
        KEY_STAGE: STAGE_VALUE[stage],
    }


def restamp_stage(
    config_artifact: dict[str, Any] | None, stage: PublishStage
) -> dict[str, Any] | None:
    """Return a copy of ``config_artifact`` with ``engine_ext.stage`` set for ``stage``.

    No-op (returns the input unchanged) when there is no composed artifact — the
    ARCA mount path pins ``migration_path`` instead of ``config_artifact`` — or when
    the artifact carries no ``engine_ext``. Never mutates the input: only the top
    dict and its ``engine_ext`` are shallow-copied; the rest is shared (read-only).
    """
    if not config_artifact or KEY_STAGE not in config_artifact.get("engine_ext", {}):
        # No artifact, or an artifact whose engine_ext wasn't backend-enriched at
        # build — nothing to re-stamp. (The build injection always seeds ``stage``,
        # so a missing ``stage`` means this isn't an enriched external artifact.)
        return config_artifact
    restamped = dict(config_artifact)
    restamped["engine_ext"] = {**config_artifact["engine_ext"], KEY_STAGE: STAGE_VALUE[stage]}
    return restamped


#: The ``engine_overrides`` key holding the per-stage-variant channel config. Only
#: this sub-key is overlaid per stage; every other override knob is stage-invariant.
KEY_CHANNELS = "channels"


def apply_engine_overrides(
    config_artifact: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Overlay a stage's ``engine_overrides`` channel config onto ``config_artifact``.

    Returns a copy whose ``engine_overrides`` has its ``channels`` portion replaced
    by ``overrides``'s ``channels`` — every other (stage-invariant) override knob in
    the base is preserved. ``overrides`` is the reader's output for the stage:
    ``{"channels": {...}}`` to set channels, or ``{}`` to clear them.

    No-op (returns the input unchanged) when:
    - ``config_artifact`` is ``None`` — the ARCA mount path pins ``migration_path``,
      not a composed artifact; or
    - ``overrides`` is ``None`` — no stored stage overrides (pre-feature records),
      so the base artifact is delivered as-is.

    Never mutates the input: only the top dict and its ``engine_overrides`` are
    shallow-copied; the rest is shared (read-only). Mirrors :func:`restamp_stage`.
    """
    if config_artifact is None or overrides is None:
        return config_artifact
    base_overrides = config_artifact.get("engine_overrides") or {}
    # Replace ONLY the channels key — channels is the single per-stage-variant knob,
    # so the stage fetch is authoritative for it while every other (stage-invariant)
    # base knob is preserved. An ``overrides`` without ``channels`` (e.g. ``{}``)
    # therefore clears channels; ``{"channels": ...}`` sets them. Any non-channel key
    # the reader might carry is deliberately ignored (contract is channels-only).
    merged = {k: v for k, v in base_overrides.items() if k != KEY_CHANNELS}
    if KEY_CHANNELS in overrides:
        merged[KEY_CHANNELS] = overrides[KEY_CHANNELS]
    applied = dict(config_artifact)
    applied["engine_overrides"] = merged
    return applied
