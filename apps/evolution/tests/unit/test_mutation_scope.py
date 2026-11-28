"""Shared selector/frozen authorization contract, including multi-member bypasses."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from avernet_genome.gene_paths import skill_path
from avernet_genome.mutation_scope import (
    FrozenMutationScope,
    mutation_problems,
    parse_scope,
    resolve_scope,
    scope_json,
)
from avernet_genome.patch_models import GenomePatch
from pydantic import ValidationError

from avernet_evolution.core.evolution_strategy import (
    InMemoryStrategyRegistry,
    VersionExists,
    registration_from_json,
)


def selectors(gene="skills", include="all", **kwargs):
    return parse_scope(
        [
            {
                "gene": gene,
                "include": include,
                "operations": ["update"],
                **({"origin": "local"} if gene == "skills" else {}),
                **kwargs,
            }
        ]
    )


def inventory():
    return NS(
        persona=[NS(type="SOUL.md"), NS(type="RULES.md")],
        skills=[
            NS(name="a-b", origin="local"),
            NS(name="center", origin="center"),
            NS(name="repo", origin="repo"),
            NS(name="other", origin="local"),
        ],
    )


def patch(ops):
    return GenomePatch.model_validate(
        {
            "patch_schema": 1,
            "base": "sha256:" + "a" * 64,
            "ops": ops,
            "rationale": "evidence backed",
            "evidence": ["episode:e1"],
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "repo"},
        {"origin": "center"},
        {"operations": ["add"]},
        {"operations": []},
        {"include": []},
        {"include": ["a/b"]},
        {"include": [".."]},
        {"include": [1]},
        {"surprise": True},
        {"gene": "memory"},
    ],
)
def test_selectors_fail_closed(changes):
    with pytest.raises(ValidationError):
        selectors(**changes)


def test_raw_names_exclusions_ownership_and_policy_intersection():
    capability = selectors() + selectors("persona")
    requested = selectors(exclude=["other"]) + selectors("persona", include=["SOUL.md"])
    policy = NS(
        mutable_genes=["persona", "skills/a-b/SKILL.md", "skills/center", "skills/repo"],
        pins=["persona/SOUL.md"],
    )
    scope = resolve_scope(inventory(), policy, capability, requested)
    assert scope.targets == ("skills/a-b/SKILL.md",)
    assert scope_json(requested)[0]["origin"] == "local"
    assert FrozenMutationScope.model_validate_json(scope.model_dump_json()) == scope
    assert not scope.permits("skills/a-b/scripts/run.py")
    assert not scope.permits("skills/center/SKILL.md")
    # Legacy allowed_genes and live governance remain additional ceilings, not grants.
    assert not resolve_scope(inventory(), policy, capability, requested, ("persona",)).targets
    assert not resolve_scope(inventory(), policy, capability, requested, platform_mutable=()).targets


def test_pinned_member_does_not_block_unrelated_members_but_cannot_be_smuggled():
    scope = resolve_scope(
        inventory(), NS(mutable_genes=["skills"], pins=["skills/a-b/scripts"]), selectors(), None
    )
    good = {"op": "skill.update", "name": "a-b", "files": {"SKILL.md": "updated"}}
    assert not mutation_problems(patch([good]), scope, inventory())
    bad = {**good, "files": {"SKILL.md": "updated", "scripts/run.py": "changed"}}
    assert mutation_problems(patch([bad]), scope, inventory())
    assert mutation_problems(patch([good]), FrozenMutationScope(targets=()))


@pytest.mark.parametrize(
    "op",
    [
        {"op": "skill.add", "name": "a-b", "files": {"SKILL.md": "new"}},
        {"op": "skill.remove", "name": "a-b"},
        {"op": "persona.remove", "type": "SOUL.md"},
        {"op": "persona.add", "type": "SOUL.md", "content": "new"},
        {"op": "memory.set_mode", "mode": "merge"},
    ],
)
def test_update_scope_never_authorizes_lifecycle_or_other_domains(op):
    scope = FrozenMutationScope(targets=("skills/a-b", "persona/SOUL.md"))
    assert mutation_problems(patch([op]), scope)


def test_backend_inventory_is_an_independent_ownership_ceiling():
    forged = FrozenMutationScope(targets=(skill_path("center"),))
    op = {"op": "skill.update", "name": "center", "files": {"SKILL.md": "overwrite"}}
    assert mutation_problems(patch([op]), forged, inventory())


@pytest.mark.asyncio
async def test_capability_is_part_of_immutable_strategy_version():
    registry = InMemoryStrategyRegistry()
    original = registration_from_json(
        {
            "id": "reference/behavior-refinement",
            "version": "1.0.0",
            "runtime": {"kind": "in_process", "package": "reference.behavior"},
            "mutation_capability": scope_json(selectors("persona")),
        }
    )
    first = await registry.register(original, {})
    assert await registry.register(original, {}) == first
    with pytest.raises(VersionExists):
        await registry.register(replace(original, mutation_capability=selectors()), {})


def test_raw_skill_name_is_encoded_once_and_member_diffs_cannot_escape():
    name = "raw%name"
    spec = NS(persona=[], skills=[NS(name=name, origin="local")])
    scope = resolve_scope(spec, NS(mutable_genes=["skills"], pins=[]), selectors(include=[name]), None)
    assert scope.targets == (skill_path(name),)
    assert "%25" in scope.targets[0]
    # Patch names themselves follow the stricter Genome schema; canonical paths
    # still need to handle raw names without double encoding.
    assert scope.permits(skill_path(name, "refs/a b.md"))
