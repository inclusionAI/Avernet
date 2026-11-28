"""Update-only mutation authorization shared by Evolution and the Genome Registry.

Selectors use raw names; frozen targets use canonical gene paths. Neither is
part of a revision/patch hash. None means the legacy path-only contract, while
an empty frozen scope grants nothing. SkillSet/Center/repo mutation is absent.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    TypeAdapter,
    model_validator,
)

from avernet_genome.gene_paths import (
    covers,
    is_canonical_gene_path,
    overlaps,
    persona_path,
    skill_path,
    split,
)
from avernet_genome.patch_models import FileEdit, GenomePatch, SkillUpdate
from avernet_genome.spec_models import GenomePolicy, GenomeSpec

Names = Annotated[tuple[StrictStr, ...], Field(min_length=1, max_length=50)]


class MutationSelector(BaseModel):
    """One category selected by a binding or a strategy's maximum capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    gene: Literal["persona", "skills"]
    include: Literal["all"] | Names
    exclude: tuple[StrictStr, ...] = ()
    operations: tuple[Literal["update"], ...] = Field(min_length=1, max_length=1)
    origin: Literal["local"] | None = None

    @model_validator(mode="after")
    def valid(self) -> MutationSelector:
        if (self.gene == "skills") != (self.origin == "local"):
            raise ValueError("skills require origin=local; persona has no origin")
        names = self.exclude + (() if self.include == "all" else self.include)
        if any(
            not name or "/" in name or "\\" in name or name in (".", "..")
            for name in names
        ):
            raise ValueError("scope names must be nonempty raw entry names, not paths")
        return self

    def selects(self, gene: str, name: str) -> bool:
        return (
            self.gene == gene
            and name not in self.exclude
            and (self.include == "all" or name in self.include)
        )


Scope = tuple[MutationSelector, ...]
_SCOPE = TypeAdapter(Annotated[Scope, Field(min_length=1, max_length=50)])


def parse_scope(value: object) -> Scope:
    """Reject unknown fields, origins, operations and empty authorization."""
    return _SCOPE.validate_python(value)


def scope_json(scope: Scope | None) -> list[dict] | None:
    return (
        None
        if scope is None
        else [
            selector.model_dump(mode="json", exclude_none=True)
            for selector in parse_scope(scope)
        ]
    )


class FrozenMutationScope(BaseModel):
    """Concrete update targets and deny rules resolved against the Run's parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    targets: tuple[StrictStr, ...] = Field(
        description="Canonical existing persona/local Skill paths authorized at Run creation; empty grants nothing."
    )
    pins: tuple[StrictStr, ...] = Field(
        default=(),
        description="Canonical immutable paths excluded from the frozen update targets.",
    )
    operations: tuple[Literal["update"], ...] = Field(
        default=("update",),
        description="Permitted mutation operations; only update of existing entries is supported.",
    )

    @model_validator(mode="after")
    def valid(self) -> FrozenMutationScope:
        if self.operations != ("update",):
            raise ValueError("only update is supported")
        if any(not is_canonical_gene_path(path) for path in self.targets + self.pins):
            raise ValueError("frozen scope requires canonical gene paths")
        for path in self.targets:
            category, names = split(path)
            if (
                category not in ("persona", "skills")
                or not names
                or (category == "persona" and len(names) != 1)
            ):
                raise ValueError(
                    "a frozen target names an existing persona file or local skill"
                )
        return self

    def permits(self, path: str) -> bool:
        return any(covers(target, path) for target in self.targets) and not any(
            overlaps(pin, path) for pin in self.pins
        )


def resolve_scope(
    spec: GenomeSpec,
    policy: GenomePolicy,
    capability: Scope,
    requested: Scope | None,
    allowed_genes: tuple[str, ...] = (),
    *,
    platform_mutable: tuple[str, ...] | None = None,
    platform_pins: tuple[str, ...] = (),
) -> FrozenMutationScope:
    """Intersect capability, binding, parent inventory/ownership and policy.

    Legacy allowed_genes remains an additional ceiling if present. Only current
    parent entries can be selected; a future skill or newly moved active cannot
    expand this result. Child policy grants are retained as child targets.
    """
    capability = parse_scope(capability)
    requested = None if requested is None else parse_scope(requested)
    pins = tuple(sorted(set(policy.pins) | set(platform_pins)))
    targets: set[str] = set()
    inventory = [("persona", p.type, persona_path(p.type)) for p in spec.persona]
    inventory.extend(
        ("skills", s.name, skill_path(s.name))
        for s in spec.skills
        if s.origin == "local"
    )
    for category, name, path in inventory:
        if not any(s.selects(category, name) for s in capability):
            continue
        if requested is not None and not any(
            s.selects(category, name) for s in requested
        ):
            continue
        ceilings = [tuple(policy.mutable_genes)]
        if platform_mutable is not None:
            ceilings.append(platform_mutable)
        if allowed_genes:
            ceilings.append(allowed_genes)
        paths = {path}
        for ceiling in ceilings:
            paths = {
                candidate if covers(rule, candidate) else rule
                for candidate in paths
                for rule in ceiling
                if overlaps(candidate, rule)
            }
        targets.update(p for p in paths if not any(covers(pin, p) for pin in pins))
    return FrozenMutationScope(targets=tuple(sorted(targets)), pins=pins)


def mutation_problems(
    patch: GenomePatch, scope: FrozenMutationScope, spec: GenomeSpec | None = None
) -> list[str]:
    """Validate actual ops, including every member of a multi-file skill.update.

    Backend supplies the base spec to independently enforce existing/local
    ownership. Evolution uses its frozen targets before reserving a candidate.
    """
    problems: list[str] = []
    persona = {p.type for p in spec.persona} if spec is not None else None
    local = (
        {s.name for s in spec.skills if s.origin == "local"}
        if spec is not None
        else None
    )
    for index, op in enumerate(patch.ops):
        paths: list[str] = []
        if isinstance(op, FileEdit):
            category, names = split(op.target)
            if (
                category == "persona"
                and len(names) == 1
                and (persona is None or names[0] in persona)
                or category == "skills"
                and len(names) >= 2
                and (local is None or names[0] in local)
            ):
                paths = [op.target]
        elif isinstance(op, SkillUpdate) and (local is None or op.name in local):
            paths = [skill_path(op.name, diff.path) for diff in op.file_diffs or ()]
            paths.extend(skill_path(op.name, member) for member in op.files or {})
        if not paths or any(not scope.permits(path) for path in paths):
            problems.append(
                f"op {index} ({op.op}) is outside frozen mutation_scope (existing persona/local skill updates only)"
            )
    return problems
