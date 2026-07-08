"""Re-export framework fixtures so contract suites can use ``world``.

Pytest auto-discovers fixture names imported into a conftest module.
Mirrors the pattern in ``tests/endpoints/conftest.py`` (minus the
endpoint-case discovery, which contracts don't use).
"""
from __future__ import annotations

import pytest

from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401
from tests.community.framework.world import World


@pytest.fixture(scope="module")
def community_world() -> World:
    """A :class:`World` backed by the **community** profile injector.

    Lets contract suites exercise the community implementation of a Protocol
    (B3–B7) through the same consumer surface as the local-impl ``world``,
    proving the community column wires an impl that satisfies the contract.
    Module-scoped: ``build_injector(community)`` is comparatively heavy.
    """
    from agentclaw.community.di import DeployProfile, build_injector

    return World(build_injector(profile=DeployProfile.COMMUNITY))
