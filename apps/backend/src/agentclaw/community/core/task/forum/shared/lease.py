"""Distributed lease primitives shared by backend-owned BBS scheduled jobs."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.errors import Conflict

__all__ = ["DistributedLease", "DistributedLeaseUnavailable"]


class DistributedLeaseUnavailable(Conflict):
    """The distributed lease is already held by another backend instance."""


@dataclass(frozen=True, slots=True)
class DistributedLease:
    """A named cache lease token and the policy under which it was acquired."""

    key: str
    token: str
    ttl_seconds: int

    def __str__(self) -> str:
        return f"{self.key}(ttl={self.ttl_seconds}s)"
