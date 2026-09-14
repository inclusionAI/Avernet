"""Which teclaw delivery a deployment runs, and the one place it is read (W8).

A leaf: an enum, a yaml key, and the parser between them. It imports nothing
from the feature, because both of its consumers sit at the edges — the typed
config cluster in ``di/config.py``, which is imported before almost everything
else, and the composition root, which turns the mode into the objects that do
the work (``apply/delivery`` for the apply seam, the managed-files reader for
the compose seam). Keeping the vocabulary separate from the strategies is what
lets the config cluster name a mode without pulling the apply graph in behind
it.

**The mode's whole life is three steps:** a yaml scalar, a table row, an
object. It is read once at boot by :func:`teclaw_delivery_mode_from_config`,
used once per selection table in the composition root, and then gone — no
component built from it holds it, and nothing asks for it again. A
deployment-time fact is settled at deployment time; a component that kept it
would be re-deciding, on every call and in front of every bot, a decision that
cannot change while the process lives.
"""
from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

#: The yaml key under ``user_config.bot_config_manifest``::
#:
#:     user_config:
#:       bot_config_manifest:
#:         teclaw_platform_managed: true
#:
#: Still spelled as a boolean switch, because that is what deployments have
#: written and renaming a live key would silently reset every one that set it.
#: It stops being a boolean the moment it is read, below.
TECLAW_PLATFORM_MANAGED_KEY = "teclaw_platform_managed"


class TeclawDeliveryMode(StrEnum):
    """Which teclaw delivery a *deployment* runs — a boot-time fact.

    Two values, lowercase on the wire::

        TeclawDeliveryMode.PLATFORM.value == "platform"

    Each names an implementation rather than a setting, which is the point: the
    composition root looks the mode up in a table
    (``apply/delivery.TECLAW_DELIVERY_BY_MODE`` for the apply seam, and the
    compose-reader table beside it) and binds what it finds.
    """

    #: The artifact is the delivery: every construct writes platform state,
    #: the composer reads it back under the ``ownership`` map, and one
    #: whole-artifact redeliver closes an apply.
    PLATFORM = "platform"
    #: The shape teclaw ran before W8: every non-script construct after the
    #: container, through the device-backed ports ARCA uses, and no platform
    #: ownership asserted at compose time. The default, until the teclaw
    #: engine supports the ``ownership`` map.
    DEVICE = "device"


_MODE_BY_BOOL: Mapping[bool, TeclawDeliveryMode] = MappingProxyType({
    True: TeclawDeliveryMode.PLATFORM,
    False: TeclawDeliveryMode.DEVICE,
})

_MODE_BY_TEXT: Mapping[str, TeclawDeliveryMode] = MappingProxyType({
    "true": TeclawDeliveryMode.PLATFORM,
    "yes": TeclawDeliveryMode.PLATFORM,
    "on": TeclawDeliveryMode.PLATFORM,
    "1": TeclawDeliveryMode.PLATFORM,
    "false": TeclawDeliveryMode.DEVICE,
    "no": TeclawDeliveryMode.DEVICE,
    "off": TeclawDeliveryMode.DEVICE,
    "0": TeclawDeliveryMode.DEVICE,
})


def teclaw_delivery_mode_from_config(
    tree: Mapping[str, Any] | None,
) -> TeclawDeliveryMode:
    """The deployment's teclaw delivery mode, read from the ``user_config`` tree.

    ``tree`` is the merged ``user_config`` mapping; the block this reads is
    ``tree["bot_config_manifest"][TECLAW_PLATFORM_MANAGED_KEY]``.

    ============================================  =========================
    Value at that key                             Mode
    ============================================  =========================
    absent, or the block is missing/not a         ``DEVICE``
    mapping, or ``tree`` is ``None``
    ``None`` (``teclaw_platform_managed:``        ``DEVICE``
    with nothing after it)
    ``True`` / ``False``                          ``PLATFORM`` / ``DEVICE``
    ``"true"``, ``"yes"``, ``"on"``, ``"1"``      ``PLATFORM``
    ``"false"``, ``"no"``, ``"off"``, ``"0"``     ``DEVICE``
    ``0`` / ``1``                                 ``DEVICE`` / ``PLATFORM``
    anything else                                 raises ``ValueError``
    ============================================  =========================

    Called by: the typed config cluster, at boot. **This is where the boolean
    ends.**

    Strict, and the strictness is the point: YAML may hand back a string, and
    ``bool("false")`` is ``True``. A switch that turned a delivery path on
    because someone quoted ``"off"`` would fail every teclaw apply in a
    deployment whose engine has not shipped the ``ownership`` map. So only a
    boolean, the usual boolean spellings, or 0/1 are accepted; anything else
    raises at boot, where a config mistake belongs. A block that is not a
    mapping is read as absent, the way the sibling readers treat a missing
    block.

    The ``isinstance`` ladder narrows a yaml scalar to a type a table can be
    keyed by; it is parsing, not selection. ``True`` and ``1`` cannot share one
    table — Python hashes them equal — which is the whole reason there are two.
    """
    block = (tree or {}).get("bot_config_manifest") or {}
    if not isinstance(block, Mapping) or TECLAW_PLATFORM_MANAGED_KEY not in block:
        return TeclawDeliveryMode.DEVICE
    raw = block[TECLAW_PLATFORM_MANAGED_KEY]
    if raw is None:
        # ``teclaw_platform_managed:`` with nothing after it — the likeliest
        # spelling of "not set" — reads as absent, not as a malformed value.
        return TeclawDeliveryMode.DEVICE
    if isinstance(raw, bool):
        return _MODE_BY_BOOL[raw]
    if isinstance(raw, str):
        mode = _MODE_BY_TEXT.get(raw.strip().lower())
        if mode is not None:
            return mode
    elif isinstance(raw, (int, float)) and raw in (0, 1):
        return _MODE_BY_BOOL[bool(raw)]
    raise ValueError(
        f"user_config.bot_config_manifest.{TECLAW_PLATFORM_MANAGED_KEY}: "
        f"not a boolean: {raw!r}"
    )


__all__ = [
    "TECLAW_PLATFORM_MANAGED_KEY",
    "TeclawDeliveryMode",
    "teclaw_delivery_mode_from_config",
]
