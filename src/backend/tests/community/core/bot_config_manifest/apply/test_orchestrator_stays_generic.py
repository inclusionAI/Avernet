"""Structural guards that keep the engine's shape honest.

These assert properties of the *source*, not of a run. Each one protects a
promise that is easy to break by accident and impossible to notice afterwards.
"""
from __future__ import annotations

import inspect
import re

from agentclaw.community.core.bot_config_manifest.apply import (
    order,
    orchestrator,
    registry,
)
from agentclaw.community.core.bot_config_manifest.apply.materialisers import mcp
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)

def _code_of(module, *, strip_strings: bool = False) -> str:
    """Module source with comments and docstrings stripped.

    Prose *about* categories is fine and often necessary — the docstrings are
    where the reasoning lives. What must not appear is a category singled out in
    executable code.

    ``strip_strings`` also blanks ordinary string literals, for checks whose
    target words occur in caller-facing prose. The script materialiser's
    delivery note says "create, restart or **republish**", which a search for
    ``publish`` matches; that sentence is the feature working, not a violation.
    """
    source = inspect.getsource(module)
    source = re.sub(r"#[^\n]*", "", source)
    source = re.sub(r'"""(?:.|\n)*?"""', "", source)
    source = re.sub(r"'''(?:.|\n)*?'''", "", source)
    if strip_strings:
        source = re.sub(r'"[^"\n]*"', '""', source)
        source = re.sub(r"'[^'\n]*'", "''", source)
    return source


def _category_special_cases(module) -> list[str]:
    """Category names used as enum members or string literals in ``module``.

    Precise rather than substring, and the precision is load-bearing: a bare
    substring search for ``identity`` matches ``entry_identity`` and
    ``failed_by_identity`` — ordinary variables naming an *entry's* identity,
    not the ``identity`` category. A guard that cries wolf is a guard the next
    person deletes, so it looks for the two forms a real special case actually
    takes: ``ManifestCategory.X`` and a bare ``"x"`` literal.
    """
    source = _code_of(module)
    found: list[str] = []
    for category in ManifestCategory:
        if re.search(rf"\bManifestCategory\.{category.name}\b", source):
            found.append(f"ManifestCategory.{category.name}")
        if re.search(rf"""['"]{category.value}['"]""", source):
            found.append(f'"{category.value}"')
    return found


def test_the_orchestrator_names_no_category():
    """Adding a category must mean registering a materialiser, nothing else.

    The moment the orchestrator special-cases one, the registry stops meaning
    anything and every later work item adds "just one more" — which is exactly
    how a generic engine becomes six bespoke ones.
    """
    offenders = _category_special_cases(orchestrator)
    assert not offenders, (
        f"the orchestrator singles out {offenders}. Category-specific behaviour "
        "belongs in a materialiser; the orchestrator handles every construct "
        "the same way."
    )


def test_the_category_guard_would_catch_a_real_special_case():
    """The guard above is only worth having if it is capable of failing.

    A materialiser legitimately names its own category, so running the same
    detector over one must find something. Without this, a detector that had
    silently stopped matching would leave the orchestrator "passing" forever.
    """
    assert _category_special_cases(mcp), (
        "the detector found nothing in a module that certainly names its own "
        "category, so it would not catch a special case in the orchestrator "
        "either"
    )


def test_the_order_table_covers_every_construct_the_vocabulary_defines():
    """A construct with no row is one an apply would silently walk past."""
    declared = {step.construct for step in order.APPLY_ORDER}
    expected = set(ManifestCategory) | set(ManifestSection)
    assert declared == expected, (
        "APPLY_ORDER must name every category and section. Missing: "
        f"{sorted(c.value for c in expected - declared)}"
    )


def test_every_registered_materialiser_has_a_place_in_the_order():
    """A materialiser the order does not know about is unreachable.

    The reverse containment is deliberately **not** asserted: the order being
    wider than the registry is the sparse state W5/W6 close, and asserting it
    would fail today for exactly the reason the design intends.
    """
    ordered = {step.construct for step in order.APPLY_ORDER}
    registered = set(
        registry.build_materialisers(
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
    )
    assert registered <= ordered


def test_script_is_alone_in_the_phase_that_needs_no_container():
    """The property W13 depends on, pinned against a careless reordering."""
    pre = [
        step.construct
        for step in order.APPLY_ORDER
        if step.phase is order.ApplyPhase.PRE_CONTAINER
    ]
    assert pre == [ManifestSection.SCRIPT]


def test_phase_b_keeps_the_declared_category_order():
    """``identity → resources → skills → mcp``, per work-items §5."""
    on_container = [
        step.construct
        for step in sorted(order.APPLY_ORDER, key=lambda s: s.position)
        if step.phase is order.ApplyPhase.ON_CONTAINER
    ]
    assert on_container[:4] == [
        ManifestCategory.IDENTITY,
        ManifestCategory.RESOURCES,
        ManifestCategory.SKILLS,
        ManifestCategory.MCP,
    ]


def test_the_mcp_materialiser_cannot_reach_account_scoped_config():
    """Apply must never fan a per-bot change out across an owner's other bots.

    ``ac_user_mcp_config`` is keyed ``(user_id, server_code)`` and writing it
    calls ``sync_mcp_detail_to_all_bots``. That is why ``mcp[].config`` left
    schema v1 — and this makes the materialiser structurally unable to reach the
    write anyway, so the vocabulary and the code both have to be wrong before
    the fan-out is possible.
    """
    code = _code_of(mcp, strip_strings=True)
    for forbidden in (
        "update_user_unified_config",
        "write_unified_config",
        "sync_mcp_detail_to_all_bots",
    ):
        assert forbidden not in code, (
            f"the mcp materialiser reaches {forbidden}, which is account-scoped: "
            "applying one bot's manifest would change MCP configuration for "
            "every bot its owner has."
        )


def test_the_script_materialiser_triggers_no_execution():
    """Apply records delivery, not execution (§2.7).

    The tempting bug is a restart added so a script "takes effect now". The row
    is picked up by the existing #926 machinery at the next device provisioning;
    apply's job ends at the write.
    """
    from agentclaw.community.core.bot_config_manifest.apply.materialisers import script

    code = _code_of(script, strip_strings=True)
    # The things that would actually cause the script to run: a restart or an
    # upgrade reprovisions the device, and composing a payload bakes the row
    # into a start command. Named precisely rather than as loose words —
    # "publish" alone matched the delivery note's own "republish".
    for forbidden in (
        "restart_bot",
        "upgrade_bot",
        "_build_create_bot_payload",
        "BaasService",
        "publish_flow",
    ):
        assert forbidden not in code, (
            f"the script materialiser reaches {forbidden}. Apply delivers the "
            "row; the container and the engine answer for running it."
        )
