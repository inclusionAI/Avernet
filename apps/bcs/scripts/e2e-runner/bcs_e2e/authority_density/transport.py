"""Injection-only in-memory replay seam for the Bot-authority density slice.

The replay mirrors the landed dual authority family: every observation body
is a frozen business envelope with exactly the ``code``/``message``/
``request_id``/``data`` keys the family contract requires, and the payloads
carry only the response details the 42 shell assertions actually read (the
owner identity, the manager page polarity, the transfer pending round, the
renewal window, the moved ownership, and both transfer inbox rows). The
transport never opens a network, mints a principal, or names a credential
value; it records one ``(operation, obligation_id)`` pair per replayed call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contract import (
    INITIATED_EXPIRES_AT, MINE_USER_ID, OWNER_BOT_ID, OWNER_USER_ID,
    REFRESHED_EXPIRES_AT, TARGET_USER_ID,
)

REPLAY_REQUEST_PREFIX = "authority-density-replay"


@dataclass(frozen=True)
class Observation:
    """One deterministic replayed HTTP response envelope."""

    status: int
    body: Any


class DensityTransport(Protocol):
    def request(self, operation: str, obligation_id: str) -> Observation:
        """Return one deterministic observation without opening a transport."""


class ReplayTransport:
    """Offline operation-keyed replay with call recording."""

    def __init__(self, observations: dict[str, Observation]):
        self.observations = observations
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, obligation_id: str) -> Observation:
        self.calls.append((operation, obligation_id))
        if operation not in self.observations:
            raise KeyError(operation)
        return self.observations[operation]


def _envelope(status: int, message: str, data: Any) -> dict[str, Any]:
    return {
        "code": status,
        "message": message,
        "request_id": REPLAY_REQUEST_PREFIX,
        "data": data,
    }


def canonical_replay_observations() -> dict[str, Observation]:
    """The 20 frozen business operations with family-shaped envelopes."""
    return {
        "ownership.owner.read": Observation(200, _envelope(
            200, "owner ownership view is readable",
            {"bot_id": OWNER_BOT_ID, "owner_user_id": OWNER_USER_ID})),
        "managers.page.empty": Observation(200, _envelope(
            200, "manager page starts empty", {"total": 0, "items": []})),
        "managers.add.owner.rejected": Observation(409, _envelope(
            409, "the owner is never a manager operand",
            {"error_code": "owner_not_manager"})),
        "managers.add.target": Observation(200, _envelope(
            200, "target human is added as a manager", {"added": 1})),
        "managers.page.populated": Observation(200, _envelope(
            200, "manager page keeps the added manager",
            {"total": 1, "items": [TARGET_USER_ID]})),
        "managers.remove.target": Observation(200, _envelope(
            200, "manager is removed", {"removed": 1})),
        "managers.remove.absent": Observation(200, _envelope(
            200, "absent manager removal is idempotent", {"removed": 0})),
        "managers.restore.target": Observation(200, _envelope(
            200, "manager is restored before the transfer", {"added": 1})),
        "public.mine.read": Observation(200, _envelope(
            200, "mine-ensured human reads its own mine",
            {"bots": [], "total": 0})),
        "managers.add.mine": Observation(200, _envelope(
            200, "mine-ensured human is a valid add target", {"added": 1})),
        "managers.add.never": Observation(400, _envelope(
            400, "never-seen human stays unknown",
            {"error_code": "bot_authority_human_unknown"})),
        "managers.remove.mine": Observation(200, _envelope(
            200, "mine-ensured manager is removed before the transfer",
            {"removed": 1})),
        "transfer.initiate": Observation(201, _envelope(
            201, "owner initiates the ownership transfer",
            {"status": "pending", "target_owner_user_id": TARGET_USER_ID,
             "expires_at": INITIATED_EXPIRES_AT,
             "requested_at": INITIATED_EXPIRES_AT - 60_000})),
        "transfer.refresh": Observation(200, _envelope(
            200, "transfer refresh renews the same pending round",
            {"status": "pending", "expires_at": REFRESHED_EXPIRES_AT,
             "requested_at": REFRESHED_EXPIRES_AT - 60_000})),
        "transfer.target.read": Observation(200, _envelope(
            200, "target human reads the pending transfer",
            {"status": "pending", "target_owner_user_id": TARGET_USER_ID})),
        "transfer.accept": Observation(200, _envelope(
            200, "target human accepts the ownership transfer",
            {"status": "accepted"})),
        "ownership.target.read": Observation(200, _envelope(
            200, "the new owner reads the ownership view",
            {"bot_id": OWNER_BOT_ID, "owner_user_id": TARGET_USER_ID})),
        "managers.ledger.target.read": Observation(200, _envelope(
            200, "the new owner reads the transferred ledger",
            {"total": 1, "owner": {"owner_user_id": TARGET_USER_ID},
             "items": [OWNER_USER_ID]})),
        "transfers.inbox.received": Observation(200, _envelope(
            200, "the new owner lists received transfers",
            {"rows": [{"bot_id": OWNER_BOT_ID, "direction": "received",
                       "status": "accepted"}]})),
        "transfers.inbox.sent": Observation(200, _envelope(
            200, "the ex-owner lists sent transfers",
            {"rows": [{"bot_id": OWNER_BOT_ID, "direction": "sent",
                       "status": "accepted"}]})),
    }


def canonical_replay_transport() -> ReplayTransport:
    return ReplayTransport(canonical_replay_observations())
