"""Pure execution-time dependency policy for Track Latest convergence."""

from __future__ import annotations

from dataclasses import dataclass
import json

from agentclaw.community.core.repository.track_latest_types import (
    PublishedTrackLatestVersion,
    TrackLatestCandidate,
    TrackLatestCandidateFacts,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)


@dataclass(frozen=True, slots=True)
class TrackLatestDependencyDelta:
    skill_version_id: int
    claimed_mcp: frozenset[str]
    released_mcp: frozenset[str]


def eligible_track_latest_candidates(
    facts: TrackLatestCandidateFacts,
) -> tuple[TrackLatestCandidate, ...]:
    """Apply ordinary/default/direct reach policy to persisted candidate facts."""

    ordinary_pairs = {
        (fact.owner_id, fact.bot_id)
        for fact in facts.skill_sets
        if not fact.is_default and fact.owner_id and fact.bot_id
    }
    active_ordinary_pairs = {
        (fact.owner_id, fact.bot_id)
        for fact in facts.skill_sets
        if (
            not fact.is_default
            and fact.is_active
            and fact.owner_id
            and fact.bot_id
        )
    }
    default_engines = {
        fact.engine_type for fact in facts.skill_sets if fact.is_default
    }
    live_bots = {
        (fact.owner_id, fact.bot_id): fact
        for fact in facts.bots
        if not fact.is_deleted
    }
    candidates: set[tuple[str, str]] = set()
    for installation in facts.installations:
        pair = (installation.owner_id, installation.bot_id)
        bot = live_bots.get(pair)
        if bot is None:
            continue
        if pair in active_ordinary_pairs:
            candidates.add(pair)
            continue
        default_reaches = (
            None in default_engines or bot.active_engine in default_engines
        )
        if pair not in ordinary_pairs and not default_reaches:
            candidates.add(pair)

    candidates.update(active_ordinary_pairs & live_bots.keys())
    return tuple(
        TrackLatestCandidate(owner_id=owner_id, bot_id=bot_id)
        for owner_id, bot_id in sorted(candidates)
    )


def latest_dependency_delta(
    versions: tuple[PublishedTrackLatestVersion, ...],
) -> TrackLatestDependencyDelta:
    """Converge directly to latest even when intermediate tasks never ran."""

    if not versions:
        raise RuntimeError("Track Latest Skill has no PUBLISHED Version")
    current = _dependency_codes(versions[0])
    historical = frozenset().union(
        *(_dependency_codes(version) for version in versions[1:])
    )
    return TrackLatestDependencyDelta(
        skill_version_id=versions[0].skill_version_id,
        # Claim every dependency in the desired latest state. Claim is
        # idempotent and level-triggered; subtracting the previous release loses
        # dependencies when a Bot skips that intermediate task.
        claimed_mcp=current,
        released_mcp=historical - current,
    )


def _dependency_codes(version: PublishedTrackLatestVersion) -> frozenset[str]:
    if not version.metadata_json:
        raise RuntimeError("PUBLISHED Version has no dependency metadata")
    try:
        metadata = json.loads(version.metadata_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("PUBLISHED Version metadata is invalid") from exc
    dependencies = metadata.get("mcp_dependencies") if isinstance(metadata, dict) else None
    if not isinstance(dependencies, list):
        raise RuntimeError("PUBLISHED Version has incomplete dependency metadata")
    return frozenset(mcp_dependency_codes(dependencies))


__all__ = [
    "TrackLatestDependencyDelta",
    "eligible_track_latest_candidates",
    "latest_dependency_delta",
]
