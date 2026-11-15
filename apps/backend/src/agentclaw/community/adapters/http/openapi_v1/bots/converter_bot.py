"""Bot records to display-safe OpenAPI response models.

Router-local converters project explicit response fields and resolve space
summaries through the declared core port; they do not define HTTP endpoints.
"""

from __future__ import annotations

from typing import Any


from agentclaw.community.adapters.http.openapi_v1.clusters import (
    cluster_for_engine,
)
from agentclaw.community.core.bot_management.avatar import bot_avatar_url
from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.engines.registry import resolve_status_ext
from agentclaw.community.core.bot_management.expert_origin import expert_name_from_ext

from agentclaw.community.core.bot_inventory.errors import (
    BotInventoryPermissionError,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.policies.combo_policy import (
    assert_service_upgrade,
)
from agentclaw.community.core.bot_management.template_public_view import (
    template_config_for_public,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationUnsupportedError,
)

from .schemas import (
    Bot,
    BotMetadata,
)

def _require_service_capable_engine(bot_type: str, engine: str) -> None:
    if bot_type != "service":
        return
    decision = assert_service_upgrade(engine)
    if not decision.ok:
        raise ServicePublicationUnsupportedError(
            decision.reason or "engine cannot be used by a service bot"
        )


def _to_bot(
    d: dict[str, Any],
    *,
    space: dict[str, Any] | None = None,
    include_template: bool = True,
) -> Bot:
    """Adapt an internal bot ``to_dict()`` record to the public ``Bot`` schema.

    ``template_config`` on the row is the stored engine snapshot, returned
    verbatim (2026-09-01 passthrough decision — an owner-scoped face echoes
    the caller's own creation input, secrets included), detached via the core
    deep-copy helper — gated on a truthy ``template_type`` so the detail path
    (whose attach is not template-gated) honors the same "null without a
    template" contract the listings publish. ``space`` is the owner-view
    summary the listing endpoints resolve and pass in; other callers leave it
    null.

    ``include_template=False`` is what the 2026-09-01 decision demands once
    the collaborator-access migration broke its premise: the passthrough was
    justified by "the query faces are owner-scoped", so a face that is not —
    a collaborator reading a shared bot's base record or its restart result —
    must not receive the snapshot, whose ``token`` and ``thetaKey`` would
    then be somebody else's secret rather than the caller's own input. Those
    callers pass the owner the comparison resolved, and the field stays null
    for them rather than growing a parallel redaction model here.
    """
    engine = d.get("active_engine") or ""
    has_template = d.get("template_type") not in (None, "")
    return Bot(
        entity_id=d.get("entity_id") or "",
        bcn_bot_id=bcn_bot_reference(d),
        ext=resolve_status_ext(d, response_ext={}),
        avatar_url=bot_avatar_url(d),
        bot_id=d["bot_id"],
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=engine,
        cluster_name=cluster_for_engine(engine),
        bot_type=d.get("bot_type") or "",
        status=d.get("status") or "",
        owner_entity_id=d.get("owner_id") or "",
        expert_name=expert_name_from_ext(d.get("ext")),
        template_type=str(d["template_type"]) if has_template else None,
        template_config=(
            template_config_for_public(d.get("template_config"))
            if has_template and include_template
            else None
        ),
        space=space,
    )


def _to_public_space(ref: Any) -> dict[str, Any] | None:
    """Flatten a ``BusinessSpaceRef`` for pydantic coercion into ``BusinessSpace``."""
    if ref is None:
        return None
    return {"space_id": ref.space_id, "name": ref.name, "kind": ref.kind}


def _resolve_row_spaces(
    space_context: BusinessSpaceContextProtocol,
    rows: list[dict[str, Any]],
    *,
    owner_id: str,
) -> dict[str, dict[str, Any] | None]:
    """Owner-view space summary per distinct ``ac_bots.space_id``.

    Memoized on the raw space_id column: one ``bot_space`` resolution per
    distinct space per page. This is plain memo caching — the semantics
    (synthetic ``personal:<user>`` fallback, member-gated None) stay in the
    core ``BusinessSpaceContextProtocol``. A space the resolver refuses
    (missing membership row on a legacy record and the like) degrades to
    ``space: null`` for that row, mirroring how ``_list_cloud_rows`` skips a
    row the space module rejects: one bad record must not take the whole
    listing page down.
    """
    resolved: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        raw = str(row.get("space_id") or "")
        if raw in resolved:
            continue
        try:
            ref = space_context.bot_space(
                bot=row, owner_id=owner_id, current_space=None
            )
        except BotInventoryPermissionError:
            ref = None
        resolved[raw] = _to_public_space(ref)
    return resolved


def _to_bot_metadata(d: dict[str, Any]) -> BotMetadata:
    """Project a Bot record onto the deliberately display-only batch contract."""
    return BotMetadata(
        bcn_bot_id=bcn_bot_reference(d),
        avatar_url=bot_avatar_url(d),
        bot_id=d["bot_id"],
        owner_id=d.get("owner_id") or "",
        owner_name=d.get("owner_name"),
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=d.get("active_engine") or "",
        bot_type=d.get("bot_type") or "",
        status=d.get("status") or "",
        online_available=d.get("online_available"),
        live_version=d.get("live_version"),
        conversation_stage=d.get("conversation_stage") or "draft",
    )
