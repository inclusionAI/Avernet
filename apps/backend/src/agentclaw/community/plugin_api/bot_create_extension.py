"""Bot create/delete lifecycle extension seam.

Engine or product domains that need to reinterpret a create request or
participate in the deletion lifecycle bind an implementation here (an
overlay usually owns the concrete binding). The contract is deliberately
shapeless and businessless: inputs and outputs are opaque mappings whose
field names and validity rules are entirely the implementation's concern —
this layer never learns them, and ordinary requests whose ``request``
payload the implementation does not recognize are answered with ``None``.

The default binding is :class:`NoopBotCreateExtension`: a no-op at every
point, so an unextended runtime runs the shared flow exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class CreateOverride:
    """What an extension wants the shared flow to run with.

    Every field means "only the value set here replaces the caller's
    request value; ``None`` keeps the request as-is". ``provision=False``
    records the bot without its provisioning tail (the shared W8 split);
    the extension owns whatever must run after registration instead.
    """

    template_type: Optional[str] = None
    template_config: Optional[Mapping[str, Any]] = None
    engine_type: Optional[str] = None
    # The extension may retarget the flow onto a different bot id (its own
    # arbitration decided the caller converges onto an already-reserved
    # one); the flow then continues under that id. The caller's own id was
    # only allocation, never a written row, at that point (resolve is the
    # flow's first step).
    bot_id: Optional[str] = None
    provision: Optional[bool] = None
    #: The Passport identity for this bot already exists (the flow skips the
    # apply entirely) and carries the agent code the owner relationship
    # records. Returned only when this is a re-entry onto a bot whose
    # identity was minted in a flow the extension itself ran earlier.
    preissued: Optional[Mapping[str, Any]] = None


class ExtensionInvalidInput(ValueError):
    """The extension rejects the request shape/content — the create and
    auth-completion surfaces map this to their 400 family."""


class ExtensionConflict(Exception):
    """The extension cannot proceed with this request against current
    server-side state — mapped to the surfaces' conflict (409) family."""


class ExtensionBlocked(Exception):
    """The extension stops a lifecycle transition with a payload worth
    surfacing (``data``); mapped to the surfaces' 500 + data family."""

    def __init__(self, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.data = data


@runtime_checkable
class BotCreateExtension(Protocol):
    """The lifecycle points a bound extension participates in.

    All decisions run against server-side state the implementation owns;
    the shared flow only sequences the calls.
    """

    def resolve_create(
        self,
        *,
        bot_id: str,
        user_id: str,
        request: Mapping[str, Any],
    ) -> Optional[CreateOverride]:
        """Interpret a create or auth-completion request.

        Called first on both the direct-create and the authorization-
        completion entry, with the surface's raw request payload (opaque).
        ``None`` = this request is not the extension's business; the shared
        flow runs it unchanged, and every later point no-ops for it too.
        """
        ...

    def after_bot_registered(
        self, *, bot_id: str, user_id: str
    ) -> None:
        """Runs right after the bot row (plus owner relationship) became
        durable on a creation this extension resolved — before any
        provisioning tail. A missing row here means another actor removed
        the bot in the window; the implementation verifies its own state."""
        ...

    def before_delete(
        self, *, bot_id: str, owner_id: str
    ) -> None:
        """Runs before the shared delete starts, so an extension that owns
        dependents can settle them first (failures abort the delete)."""
        ...

    def on_delete_completed(
        self, *, bot_id: str, owner_id: str
    ) -> None:
        """Runs after the row is durably deleted (not on not-found)."""
        ...

    def on_delete_not_found(
        self, *, bot_id: str, owner_id: str
    ) -> None:
        """Runs when the owner-scoped lookup answered not-found — proof of
        neither deletion nor ownership; the implementation demonstrates
        both from its own state before any write."""
        ...


class NoopBotCreateExtension:
    """Local default: every request is outside the extension's business."""

    def resolve_create(self, **kwargs: Any) -> None:
        return None

    def after_bot_registered(self, **kwargs: Any) -> None:
        return None

    def before_delete(self, **kwargs: Any) -> None:
        return None

    def on_delete_completed(self, **kwargs: Any) -> None:
        return None

    def on_delete_not_found(self, **kwargs: Any) -> None:
        return None
