"""``resolve_device_provider`` — map a bot's *container* (queried from baas) to
its ``device_provider`` token.

The container is owned by **baas**, not derivable from the bot's engine. The
publish **build** phase asks baas for the bot's container (its device
``provider_type``) and maps it to a ``device_provider`` key used to select a
:class:`DeployArtifactProducer` (Task 12) and to stamp on the device binding —
replacing the old hardcoded ``"baas"`` (``publish_flow_service.py:2076``).

This module is the **pure mapping** half: ``provider_type`` (secbaas platform
string, e.g. ``"TECLAW"`` / ``"ARCA"``) → ``device_provider`` token. The I/O —
looking up the bot's baas ``bot_uuid`` (= binding ``device_id``) and querying
its devices — lives on ``BaasService.resolve_container_provider`` (it owns the
binding repo + baas client). Keeping the mapping pure makes it trivially
unit-testable.

Resolution rule (Rule 14: values injectable, no scattered ``if is_teclaw()``):

* ``provider_type`` equal to the teclaw container type → ``teclaw``
* anything else, or unknown/``None`` (bot not in baas yet — e.g. an ARCA draft
  never provisioned via baas) → the default (``baas``), preserving today's
  behavior. arca and baas both route to ``ArcaSnapshotProducer``, so non-teclaw
  bots are byte-for-byte unchanged.
"""
from __future__ import annotations

__all__ = [
    "resolve_device_provider",
    "TECLAW_DEVICE_PROVIDER",
    "TECLAW_PROVIDER_TYPE",
    "DEFAULT_DEVICE_PROVIDER",
]

# agentclaw ``device_provider`` token for the pull-based teclaw container. Sits
# alongside the existing local/daas/arca/baas vocabulary; routes to the
# compose-from-DB producer (Task 11/12). teclaw is the only external container
# type — there is no separate ``"external"`` provider value.
TECLAW_DEVICE_PROVIDER = "teclaw"

# secbaas device ``provider_type`` string for a teclaw container — the value
# secbaas ``start_device`` resolves for ``TeclawTemplateConfig``. Compared
# case-insensitively against what ``GET /bots/{uuid}/devices`` returns.
TECLAW_PROVIDER_TYPE = "TECLAW"

# Fallback when baas does not report a teclaw container — today's hardcoded
# value at ``publish_flow_service.py:2076`` and the build producer default.
DEFAULT_DEVICE_PROVIDER = "baas"


def resolve_device_provider(
    container_provider_type: str | None,
    *,
    teclaw_provider_type: str = TECLAW_PROVIDER_TYPE,
    default_provider: str = DEFAULT_DEVICE_PROVIDER,
) -> str:
    """Map a baas-reported container ``provider_type`` to a ``device_provider``.

    Args:
        container_provider_type: The device ``provider_type`` baas reports for
            the bot's container (e.g. ``"TECLAW"`` / ``"ARCA"``), or ``None``
            when baas does not know the bot yet (a draft never provisioned via
            baas, or a probe that found nothing).
        teclaw_provider_type: The ``provider_type`` value that denotes a teclaw
            container. Injectable; defaults to :data:`TECLAW_PROVIDER_TYPE`.
        default_provider: Token returned for any non-teclaw / unknown container.
            Defaults to :data:`DEFAULT_DEVICE_PROVIDER` (``baas``), today's
            behavior.

    Returns:
        :data:`TECLAW_DEVICE_PROVIDER` for a teclaw container, else
        ``default_provider``.
    """
    provider_type = (container_provider_type or "").strip().lower()
    if provider_type and provider_type == teclaw_provider_type.strip().lower():
        return TECLAW_DEVICE_PROVIDER
    return default_provider
