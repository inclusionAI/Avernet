"""Legacy internal-caller input folds for bot creation.

The internal ``/api/bots`` surface still sends shapes the public surface has
moved past: the ``aicoding`` engine alias, and top-level template fields
instead of the ``engine_properties`` bag. Both folds live here — at the shared
create seam — so ``create_flow`` works on the canonical form only.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AICODING_ENGINE_TYPE,
    CLAUDE_CODE_ENGINE_TYPE,
)
from agentclaw.community.core.bot_management.engines.registry import (
    normalize_engine_type,
)
from agentclaw.community.core.workspace.runtime_identity import (
    AICODING_ENGINE_FORM,
    ENGINE_FORM_KEY,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    # TYPE_CHECKING-only: BotCreateSpec lives in create_flow, which imports
    # this module — a runtime import would be circular.
    from agentclaw.community.core.bot_management.create_flow import BotCreateSpec


logger = get_logger()

# Legacy internal-engine values folded at the shared create seam. ``aicoding``
# is the internal implementation engine of ``claude_code``, not a product
# engine: new bots store the real engine and carry the form marker
# (``engine_form``) in their template snapshot instead (engine/form vocabulary
# split — docs/superpowers/specs/2026-08-31-engine-vocabulary-template-form-design.md).
_LEGACY_ENGINE_ALIASES = {AICODING_ENGINE_TYPE: CLAUDE_CODE_ENGINE_TYPE}


def normalize_legacy_engine_alias(spec: BotCreateSpec) -> BotCreateSpec:
    """Fold legacy internal-engine values into the real engine (old-link compat).

    Internal callers (``/api/bots``) may still send ``engine_type="aicoding"``.
    The bot is created on the real engine (``claude_code``); a template-backed
    create records the server-managed ``engine_form`` marker in the template
    snapshot so runtime/bucket routing stays equivalent. A plain no-template
    bot has no form — it is simply a plain ``claude_code`` bot. The public
    surface never reaches this: it rejects internal engines with 400.

    Idempotent: a spec already on the real engine passes through unchanged.
    """
    real_engine = _LEGACY_ENGINE_ALIASES.get(
        normalize_engine_type(spec.engine_type, default="")
    )
    if real_engine is None:
        return spec
    template_config = spec.template_config
    if spec.template_type and template_config is not None:
        template_config = {**template_config, ENGINE_FORM_KEY: AICODING_ENGINE_FORM}
    logger.info(
        "[create_flow] folded legacy engine alias: requested_engine=%s "
        "engine=%s template_type=%s form_marker_written=%s",
        spec.engine_type,
        real_engine,
        spec.template_type,
        spec.template_type and template_config is not None,
    )
    return replace(
        spec,
        engine_type=real_engine,
        template_config=template_config,
    )


def legacy_template_engine_properties(spec: BotCreateSpec) -> dict[str, Any]:
    """Fold the legacy top-level template pair into the engine_properties bag.

    Keeping the ``template_config`` key preserves legacy intent when the caller
    omits the config. ``template_type`` rides along: a template-factory
    snapshot (full identity: ``template_key`` + ``template_uid``) is rejected
    by the strategy unless ``engine_properties.template_type`` declares its
    type, and the internal surface only expresses that declaration through
    this fold — the public surface's nested bag already carries it.
    """
    return {
        "template_type": spec.template_type,
        "template_config": spec.template_config,
    }
