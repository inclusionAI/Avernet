"""Apply must never be a route around an owner-only category endpoint.

W10's spec (`specs/2026-08-31-config-manifest-service-seam`, *Apply Declares Its
Own Bars*) settled apply's bar on its own shape rather than deriving it from the
categories it touches — a derived bar needs recomputing every time a category
moves, and a bar nobody recomputes is a bar that rots.

That independence gives up one property the derived rule had for free: that
apply can never exceed what it materializes. This file recovers it as a test
rather than as a rule someone has to remember, which is exactly what W10 asked
W4 to carry.

Without it, a later well-meant "let collaborators use manifests, drop apply to
MEMBER" would silently hand MEMBER the ability to overwrite a bot's `SOUL.md`
through a manifest — which `PUT /openapi/v1/bots/{bot_id}/identity/{file_type}`
refuses them, because that operation is owner-only.
"""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION
from agentclaw.community.adapters.http.openapi_v1.admission_modes import AdmissionMode
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    OWNER_SCOPED,
    Check,
    ServiceChecked,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    build_materialisers,
)

APPLY = ("POST", "/openapi/v1/bots/{bot_id}/config-manifest/apply")

#: Which public paths write each category's area. Apply converges the same
#: state these operations do, so its bar must dominate theirs.
#:
#: ``script``'s own endpoints are ``OWNER_SCOPED``, which is scaffolding rather
#: than a ``Check`` — handled below.
_CATEGORY_WRITE_PATHS: dict[str, tuple[str, ...]] = {
    "mcp": (
        "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate",
        "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate",
    ),
    "script": (
        "/openapi/v1/bots/{bot_id}/startup-script",
    ),
    # Registered by W5/W6. Named now so that the moment a materialiser appears
    # for one, this test already knows where its endpoints are — the failure a
    # missing entry produces is loud and immediate.
    "skills": (
        "/openapi/v1/bots/{bot_id}/skills",
        "/openapi/v1/bots/{bot_id}/skills/{skill_id}",
    ),
    "identity": ("/openapi/v1/bots/{bot_id}/identity/{file_type}",),
    "resources": (
        "/openapi/v1/bots/{bot_id}/resources/upload",
        "/openapi/v1/bots/{bot_id}/resources",
    ),
    "engine_config": ("/openapi/v1/bots/{bot_id}/engine-config",),
    "cli_tools": (
        "/openapi/v1/bots/{bot_id}/cli-tools",
        "/openapi/v1/bots/{bot_id}/cli-tools/{name}",
    ),
}

#: Modes that admit no more callers than apply's own.
#:
#: ``Check(OWNER)`` + ``GRANT_CHECKED_ADDRESSED_BOT`` and ``OWNER_SCOPED`` +
#: ``GRANT_CHECKED_OWN_BOT`` admit **the same people**, which is why the
#: addressed mode is not a widening here. ``OWNER`` is unreachable by a
#: collaborator — the vocabulary is admin/member (``CollaboratorRole``) — so a
#: caller who passes ``Check(OWNER)`` on an addressed bot must *be* that bot's
#: owner. The addressing differs; the admitted set does not.
#:
#: A test comparing raw enum ordering would flag the pairing as a widening. This
#: compares the admitted set, and the reasoning is written down here so the next
#: reader does not have to re-derive it.
_MODES_NO_WIDER = frozenset(
    {
        AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
        AdmissionMode.GRANT_CHECKED_OWN_BOT,
    }
)


def _effective_level(rule: object) -> PermissionLevel | None:
    """The level a rule admits at, or ``None`` when it is not level-shaped.

    ``OWNER_SCOPED`` is scaffolding: it pins the bot to the caller's own, which
    admits exactly the owner. For dominance purposes that is ``OWNER``.
    """
    if isinstance(rule, Check):
        return rule.level
    if isinstance(rule, ServiceChecked):
        return rule.level
    if rule is OWNER_SCOPED:
        # Identity, not a name read: ``_Scaffold`` keeps its name in a private
        # ``_name`` slot and there is exactly one OWNER_SCOPED sentinel, so
        # comparing against the object is both correct and unable to silently
        # stop matching if the class changes shape. An earlier version of this
        # helper read a public ``name`` attribute that does not exist, which
        # made every OWNER_SCOPED row invisible — caught by the ``assert
        # checked`` guard below rather than by passing vacuously.
        return PermissionLevel.OWNER
    return None


def _apply_rule() -> Check:
    rule = AUTHORIZATION[APPLY]
    assert isinstance(rule, Check), "apply must carry an enforcing Check row"
    return rule


def test_apply_declares_the_bar_w10_settled():
    """The row itself, so a change to it is a change to this test."""
    rule = _apply_rule()
    assert rule.level is PermissionLevel.OWNER
    assert rule.edit_lock is not None, "apply is a broad mutation; it takes the lock"
    assert ADMISSION[APPLY] is AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT


@pytest.mark.parametrize(
    "construct",
    sorted(
        c.value
        for c in build_materialisers(
            script_service=object(),
            activation_service=object(),
            mcp_auth_service=object(),
            identity_service=object(),
            upload_service=object(),
            capability_reader=object(),
            package_validator=object(),
            entry_fetcher=object(),
            resource_service=object(),
            cli_tool_service=object(),
        )
    ),
)
def test_apply_bar_dominates_every_category_it_can_materialise(construct):
    """For every category apply can write, apply's bar is at least as high.

    Parametrized over the **registry**, not over a hand-written list: when W5
    registers ``skills``, this test starts covering ``skills`` with no edit. That
    is what makes it a safety net rather than a snapshot of today.
    """
    apply_level = _apply_rule().level
    paths = _CATEGORY_WRITE_PATHS.get(construct)
    assert paths is not None, (
        f"'{construct}' has a materialiser but no entry in _CATEGORY_WRITE_PATHS. "
        "Add the public paths that write its area so its bar can be compared."
    )

    checked = 0
    for (method, path), rule in AUTHORIZATION.items():
        if path not in paths or method in {"GET", "HEAD", "OPTIONS"}:
            continue
        level = _effective_level(rule)
        if level is None:
            continue
        checked += 1
        assert apply_level >= level, (
            f"apply admits at {apply_level.name} but {method} {path} requires "
            f"{level.name}: applying a manifest would be a way around that "
            f"operation's own bar."
        )

    assert checked, (
        f"no write operation found for '{construct}' — the paths in "
        "_CATEGORY_WRITE_PATHS are stale, so this test is asserting nothing."
    )


def test_apply_admission_mode_is_no_wider_than_the_categories_it_writes():
    """Apply must not admit a caller shape the category endpoints refuse.

    Both grant-checked modes are acceptable, for the reason ``_MODES_NO_WIDER``
    records: with ``Check(OWNER)``, addressed and own-bot admit the same people.
    What this refuses is apply drifting to an open or grant-filtered mode, which
    genuinely would be wider.
    """
    assert ADMISSION[APPLY] in _MODES_NO_WIDER

    for construct in build_materialisers(
        script_service=object(),
        activation_service=object(),
        mcp_auth_service=object(),
        identity_service=object(),
        upload_service=object(),
        capability_reader=object(),
        package_validator=object(),
        entry_fetcher=object(),
        resource_service=object(),
        cli_tool_service=object(),
    ):
        for path in _CATEGORY_WRITE_PATHS[construct.value]:
            for (method, candidate), mode in ADMISSION.items():
                if candidate != path or method in {"GET", "HEAD", "OPTIONS"}:
                    continue
                assert mode in _MODES_NO_WIDER, (
                    f"{method} {path} admits via {mode}, which apply's "
                    f"{ADMISSION[APPLY]} cannot be compared against — the "
                    "dominance argument needs re-making."
                )


def test_the_reads_sit_beside_the_manifest_read():
    """Reports are read at the same bar as the document they describe."""
    for path in (
        "/openapi/v1/bots/{bot_id}/config-manifest/last-apply",
        "/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}",
    ):
        rule = AUTHORIZATION[("GET", path)]
        assert isinstance(rule, Check)
        assert rule.level is PermissionLevel.MEMBER
