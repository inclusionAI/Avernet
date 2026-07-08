"""Verify Lifecycle discovery returns the expected participants in local mode.

Builds a real injector via ``build_injector(...)`` with the local-mode
testing modules and asserts that ``discover_lifecycle_participants``
finds every component that R11 migrated to the ``Lifecycle`` Protocol.

This is the regression catch for "someone added a startup hook but
forgot to make their component a ``Lifecycle`` participant" — if a
component is supposed to drive boot work but discovery doesn't see it,
the lifespan won't call its hooks and the work silently disappears.

The expected set is exhaustive for the R11 migration. If a new component
needs a lifecycle hook, add its class name to ``_EXPECTED_PARTICIPANTS``
when its migration lands.
"""
from __future__ import annotations

import pytest

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.kernel.lifecycle import Lifecycle, discover_lifecycle_participants


# Set of class names every local-mode boot must produce as Lifecycle
# participants. Discovery is allowed to return additional participants
# (any component that opts in via LifecycleBase), but the names below
# are non-negotiable — losing any of them means part of R11's boot
# pipeline went dark.
_EXPECTED_PARTICIPANTS: frozenset[str] = frozenset({
    "SqliteDB",                  # Phase 1: schema bootstrap
    "LocalDeviceLifecycle",      # Phase 2: orphan reallocation + symlink restore
    "SkillScanService",          # Phase 2: scanner + daily-task schedulers
    # GitSyncService is intentionally NOT here: it requires the skills-repo URL
    # from the secret store and fails construction (skipped by discovery) when
    # absent — which is the case in the test profile (and community). It only
    # participates where the corp secret store provides the repo URL.
    "SkillCenterSyncService",    # Phase 2: bootstrap + periodic sync
    "SkillSymlinkListener",      # Phase 2: self-subscribe to event bus
    "CronAutoSetupListener",     # Phase 2: self-subscribe to event bus
    "DesktopBotLifecycle",       # Phase 2: recover PENDING desktop bots
    "BaasPublishTaskLifecycle",  # Phase 2: register durable BaaS publish handlers
})


@pytest.fixture(scope="module")
def participants() -> list:
    injector = build_injector(profile=DeployProfile.TEST)
    return discover_lifecycle_participants(injector)


@pytest.mark.unit
def test_discovery_returns_only_lifecycle_satisfying_instances(participants) -> None:
    """Every returned participant must satisfy the Lifecycle Protocol."""
    bad = [
        type(p).__name__
        for p in participants
        if not isinstance(p, Lifecycle)
    ]
    assert not bad, (
        "discover_lifecycle_participants returned non-Lifecycle instances: "
        f"{bad}"
    )


@pytest.mark.unit
def test_discovery_dedupes_by_instance_identity(participants) -> None:
    """No two entries share the same ``id()``."""
    ids = [id(p) for p in participants]
    assert len(ids) == len(set(ids)), (
        "discover_lifecycle_participants returned duplicate instances; "
        "the id-based dedup is broken."
    )


@pytest.mark.unit
def test_discovery_contains_every_expected_participant(participants) -> None:
    """The expected R11-migrated components must all be discoverable.

    Failures here mean a Lifecycle participant fell off — either its
    DI binding was removed, its impl no longer inherits LifecycleBase,
    or the discovery walk can't reach it. The lifespan will silently
    skip whatever's missing, so this test exists to make that loud.
    """
    found = {type(p).__name__ for p in participants}
    missing = _EXPECTED_PARTICIPANTS - found
    assert not missing, (
        f"Expected Lifecycle participants missing from discovery: "
        f"{sorted(missing)}.\n"
        f"Discovered: {sorted(found)}"
    )
