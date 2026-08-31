"""The table is only worth having if it names what the routers actually call.

A table listing functions that merely *resemble* the router's would be worse
than no table: it would document a guarantee that does not hold, and a reviewer
reading it would stop looking. So the assertions here are ``is``, not ``==`` —
two functions with identical behaviour are precisely the failure this file
exists to catch, because that is what drift looks like on the day it starts.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from agentclaw.community.core.bot_config_surface.coords import BotConfigCoords
from agentclaw.community.core.bot_config_surface.table import CONFIG_SURFACE
from agentclaw.community.core.resources.service import InvalidResourcePathError
from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError

#: The categories manifest apply touches. ``engine_config`` is here although
#: W4 excludes it from the first phase — the row must exist before its
#: materializer returns, or nobody discovers it is missing until then.
EXPECTED_CATEGORIES = frozenset(
    {"identity", "resources", "skills", "mcp", "engine_config"}
)


def test_table_covers_exactly_the_categories_apply_touches():
    assert set(CONFIG_SURFACE) == EXPECTED_CATEGORIES, (
        "CONFIG_SURFACE must carry one row per category manifest apply can "
        "materialize. Missing: "
        f"{sorted(EXPECTED_CATEGORIES - set(CONFIG_SURFACE))}; unexpected: "
        f"{sorted(set(CONFIG_SURFACE) - EXPECTED_CATEGORIES)}"
    )


def test_every_row_is_keyed_by_its_own_category():
    for key, row in CONFIG_SURFACE.items():
        assert row.category == key


# ── The same object, not a lookalike ──────────────────────────────────────────


def test_resources_router_calls_the_table_s_objects():
    mod = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.resources.router"
    )

    row = CONFIG_SURFACE["resources"]
    assert mod._safe_path is row.validators[0]
    assert mod._require_path is row.validators[1]
    assert mod._file_coords is row.from_record
    # ``_reject_read_only`` stays in the router on purpose — it maps the verdict
    # to the surface's own 403 body. What must be shared is the *decision*.
    assert mod.is_write_forbidden is row.validators[2]


def test_identity_router_calls_the_table_s_objects():
    mod = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.identity.router"
    )

    row = CONFIG_SURFACE["identity"]
    assert mod.identity_coords_from_record is row.from_record
    assert mod.identity_physical_file_name is row.validators[0]


def test_skills_router_calls_the_table_s_objects():
    mod = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.skills.router"
    )

    row = CONFIG_SURFACE["skills"]
    assert mod._require_addressed_bot is row.validators[0]


def test_engine_config_router_calls_the_table_s_object():
    mod = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.bots.engine_config"
    )

    assert mod._engine_config_coords is CONFIG_SURFACE["engine_config"].from_record


# ── Usable with no request, no app, no DI container ───────────────────────────
#
# This is the capability the whole feature exists to deliver, so it is proven
# rather than assumed. Nothing below builds an app, resolves a dependency, or
# touches the injector.


def test_validators_take_no_repository_or_service():
    """Record-freeness, asserted structurally rather than by hoping.

    A validator that grew a ``bot_repo`` or ``bot_service`` parameter would
    still pass its own unit test while quietly becoming unusable at W13's
    preflight, where there is no record to hand it. Catch it at the signature.
    """
    forbidden = {"bot_repo", "bot_service", "repo", "service", "session", "db"}
    for name, row in CONFIG_SURFACE.items():
        for validator in row.validators:
            params = set(inspect.signature(validator).parameters)
            assert not (params & forbidden), (
                f"{name}: validator {validator.__name__} takes "
                f"{sorted(params & forbidden)} — it cannot run at create-time "
                "preflight, where no bot record exists"
            )


def test_from_spec_builds_coords_with_no_record_anywhere():
    """W13's preflight, rehearsed before W13 exists.

    Every row's ``from_spec`` is called with request parameters alone. If this
    ever needs a bot record to pass, the split did not happen and
    create-with-manifest will be forced into a second validation copy.
    """
    built = {
        "identity": CONFIG_SURFACE["identity"].from_spec("bot-1", "owner-1"),
        "resources": CONFIG_SURFACE["resources"].from_spec("bot-1", "owner-1", "arca"),
        "skills": CONFIG_SURFACE["skills"].from_spec("bot-1", "owner-1"),
        "mcp": CONFIG_SURFACE["mcp"].from_spec("bot-1", "owner-1"),
        "engine_config": CONFIG_SURFACE["engine_config"].from_spec(
            "bot-1", "owner-1", entity_id="e-1", entity_type=None, engine_type=None
        ),
    }
    assert set(built) == EXPECTED_CATEGORIES
    for name, coords in built.items():
        assert isinstance(coords, BotConfigCoords), name
        assert coords.bot_id == "bot-1", name
        assert coords.owner_id == "owner-1", name
        assert coords.entity_id, name


def test_validators_run_against_spec_built_coords():
    """The validators paired with those coordinates, still with no record."""
    coords = CONFIG_SURFACE["resources"].from_spec("bot-1", "owner-1", "arca")
    assert coords.engine_type == "arca"

    safe_path, require_path, write_forbidden = CONFIG_SURFACE["resources"].validators
    assert safe_path("/docs/./a.txt") == "docs/a.txt"
    assert require_path("docs/a.txt") == "docs/a.txt"
    assert write_forbidden(".hidden/a.txt") is True
    assert write_forbidden("docs/a.txt") is False

    physical_name = CONFIG_SURFACE["identity"].validators[0]
    assert physical_name("SOUL") == "SOUL.md"


def test_identity_and_mcp_carry_no_engine():
    """``engine_type`` states an absence rather than inventing a default."""
    for category in ("identity", "mcp", "skills"):
        coords = CONFIG_SURFACE[category].from_spec("bot-1", "owner-1")
        assert coords.engine_type is None, category


# ── The refusals, unchanged by the move ───────────────────────────────────────


@pytest.mark.parametrize("bad", ["../etc", "docs/../../etc", "..", "a/../b"])
def test_workspace_path_still_refuses_escapes(bad):
    safe_path = CONFIG_SURFACE["resources"].validators[0]
    with pytest.raises(InvalidResourcePathError):
        safe_path(bad)


def test_workspace_path_still_refuses_the_root():
    require_path = CONFIG_SURFACE["resources"].validators[1]
    with pytest.raises(InvalidResourcePathError):
        require_path("/")


def test_read_only_policy_still_walks_every_ancestor():
    """``.private/file.md`` is refused although its leaf is an ordinary name."""
    write_forbidden = CONFIG_SURFACE["resources"].validators[2]
    assert write_forbidden(".private/file.md") is True
    assert write_forbidden("SOUL.md") is True
    assert write_forbidden("docs/SOUL.md") is False


def test_addressed_bot_mismatch_is_masked_as_not_found():
    require_addressed = CONFIG_SURFACE["skills"].validators[0]
    require_addressed({"bolt_id": "bot-1"}, "bot-1")
    with pytest.raises(LocalSkillNotFoundError):
        require_addressed({"bolt_id": "bot-2"}, "bot-1")
