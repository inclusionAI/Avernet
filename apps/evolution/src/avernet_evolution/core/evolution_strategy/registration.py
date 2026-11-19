"""The registration record and its checks (03-strategy.zh-CN.md §2.2, §3.2, §3.3).

A registration says two things the platform must know without running the strategy's
code: where ``run(ctx)`` executes (``runtime``) and what it may use (``needs``). The
checks here are the ones §3.2 performs on upload and §3.3 performs when a binding is
written, so a mismatch is rejected at configuration time instead of mid-run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from avernet_genome.canonical import tree_digest
from avernet_genome.gene_paths import covers, op_gene_path, overlaps
from avernet_genome.patch_models import GenomePatch

from .catalog import DEFAULT_CATALOG, Catalog, Engine
from .params_schema import validate as validate_against_schema

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
STRATEGY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")


class RuntimeKind(StrEnum):
    """Where the strategy's own ``run(ctx)`` executes (§8.2)."""

    IN_PROCESS = "in_process"
    JOB_WORKER = "job_worker"


class ConformanceState(StrEnum):
    """Result of the conformance kit (§8.4)."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class Runtime:
    """``in_process`` names a package; ``job_worker`` names a digest-pinned image."""

    kind: RuntimeKind
    image: str | None = None
    package: str | None = None

    def problems(self) -> list[str]:
        if self.kind is RuntimeKind.IN_PROCESS:
            if not self.package:
                return ["runtime.package is required for in_process"]
            if self.image:
                return ["runtime.image is only for job_worker"]
            return []
        if not self.image:
            return ["runtime.image is required for job_worker"]
        if self.package:
            return ["runtime.package is only for in_process"]
        if "@sha256:" not in self.image:
            return ["runtime.image must be pinned by digest, e.g. 'registry/app@sha256:…'"]
        return []


@dataclass(frozen=True)
class AgentDefinitionRef:
    """An agent definition shipped with the strategy: an engine plus a project path."""

    engine: Engine
    path: str
    digest: str | None = None


@dataclass(frozen=True)
class AgentDefinition:
    """A stored, validated definition; the provider loads exactly these bytes (§2.4)."""

    strategy: str
    strategy_version: str
    name: str
    engine: Engine
    digest: str
    validated_by: str
    files: tuple[tuple[str, bytes], ...] = ()

    def file_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.files)


@dataclass(frozen=True)
class StrategyRegistration:
    """What the author writes in ``strategy.json`` (§2.2)."""

    id: str
    version: str
    runtime: Runtime
    needs: dict[str, dict[str, Any]] = field(default_factory=dict)
    params_schema: dict[str, Any] | None = None

    def definition_refs(self) -> dict[str, AgentDefinitionRef]:
        """``needs["agents@1"].definitions`` parsed into typed refs."""

        raw = (self.needs.get("agents@1") or {}).get("definitions") or {}
        return {
            name: AgentDefinitionRef(
                engine=Engine.of(spec.get("engine", "")),
                path=str(spec.get("path", "")),
                digest=spec.get("digest"),
            )
            for name, spec in raw.items()
        }

    def declared_capabilities(self) -> list[str]:
        return sorted(self.needs.keys())


@dataclass(frozen=True)
class ConformanceStatus:
    status: ConformanceState
    kit_version: str
    report_artifact: str | None = None


@dataclass(frozen=True)
class RegisteredStrategy:
    """What the registry stores and returns (§2.2)."""

    registration: StrategyRegistration
    engines: tuple[Engine, ...]
    conformance: ConformanceStatus
    enabled: bool = True
    registered_at: datetime | None = None
    definitions: tuple[AgentDefinition, ...] = ()

    def definition(self, name: str) -> AgentDefinition | None:
        return next((entry for entry in self.definitions if entry.name == name), None)


def definition_digest(files: dict[str, bytes]) -> str:
    """Content hash of a definition directory, as the registry stores it."""

    return tree_digest(sorted(files.items()))


def validate_agent_definition(engine: Engine, files: dict[str, bytes]) -> list[str]:
    """The engine's definition contract, checked at registration (§3.2 step 3)."""

    problems: list[str] = []
    if not files:
        return ["definition is empty"]
    if engine is Engine.OPENCLAW:
        if "SKILL.md" not in files:
            problems.append("an openclaw definition must contain SKILL.md")
        elif not files["SKILL.md"].strip():
            problems.append("SKILL.md is empty")
        config = files.get("config.json")
        if config is not None:
            try:
                json.loads(config)
            except ValueError as exc:
                problems.append(f"config.json is not valid JSON: {exc}")
        return problems
    if engine is Engine.CLAUDE_CODE:
        if "CLAUDE.md" not in files and "SKILL.md" not in files:
            problems.append("a claude-code definition must contain CLAUDE.md or SKILL.md")
        return problems
    return [f"no definition contract for engine {engine.value}"]


def validate_registration(
    registration: StrategyRegistration,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
    definitions: dict[str, dict[str, bytes]] | None = None,
) -> list[str]:
    """Every check of §3.2 that can run before the conformance kit.

    ``definitions`` maps agent name → the uploaded directory's files; when a name is
    absent the upload is reported as missing, exactly as ``POST /evolution/strategies``
    does with a missing ``definition:<name>`` part.
    """

    problems: list[str] = []

    if not STRATEGY_ID.match(registration.id):
        problems.append(f"id {registration.id!r} is not '<owner>/<name>'")
    if not SEMVER.match(registration.version):
        problems.append(f"version {registration.version!r} is not semver")
    problems.extend(registration.runtime.problems())

    for key, arguments in registration.needs.items():
        entry = catalog.get(key)
        if entry is None:
            problems.append(f"unknown capability {key!r}")
            continue
        problems.extend(f"{key}: {problem}" for problem in entry.validate_arguments(arguments))

    if registration.params_schema is not None:
        problems.extend(
            f"params_schema: {problem}"
            for problem in validate_against_schema(registration.params_schema, {"type": "object"})
        )

    definitions = definitions or {}
    for name, ref in registration.definition_refs().items():
        provider = _agents_provider(catalog, ref.engine)
        if provider is None:
            problems.append(
                f"agents@1 has no provider for engine {ref.engine.value}; "
                f"definition {name!r} cannot run there"
            )
        files = definitions.get(name)
        if files is None:
            problems.append(f"agent definition {name!r} has no uploaded part 'definition:{name}'")
            continue
        problems.extend(
            f"agent definition {name!r}: {problem}"
            for problem in validate_agent_definition(ref.engine, files)
        )
    return problems


def _agents_provider(catalog: Catalog, engine: Engine) -> str | None:
    return catalog.provider("agents@1", engine)


def derive_engines(registration: StrategyRegistration) -> tuple[Engine, ...]:
    """The engines a version can run on: the distinct engines of its definitions."""

    refs = registration.definition_refs()
    return tuple(sorted({ref.engine for ref in refs.values()}, key=lambda engine: engine.value))


def check_engine(
    registration: StrategyRegistration,
    engine: Engine,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
) -> list[str]:
    """The registration half of a binding check (§3.3): empty when it can run there."""

    problems: list[str] = []
    for key in registration.declared_capabilities():
        entry = catalog.get(key)
        if entry is None:
            problems.append(f"unknown capability {key!r}")
            continue
        if entry.serves(engine) is None:
            problems.append(f"{key} has no provider for engine {engine.value}")

    refs = registration.definition_refs()
    if refs and all(ref.engine is not engine for ref in refs.values()):
        declared = ", ".join(sorted(ref.engine.value for ref in refs.values()))
        problems.append(
            f"none of the agents@1 definitions target engine {engine.value} (they target {declared})"
        )
    return problems


def check_params(registration: StrategyRegistration, params: dict[str, Any] | None) -> list[str]:
    """Validate a binding's ``params`` against ``params_schema`` at binding time (§3.3)."""

    if registration.params_schema is None:
        return []
    return validate_against_schema(params if params is not None else {}, registration.params_schema)


def check_allowed_genes(
    patch: GenomePatch, mutable_genes: list[str], pins: list[str] | None = None
) -> list[str]:
    """Every op must stay inside ``mutable_genes`` and outside ``pins`` (§3.3, §8.4)."""

    pinned = [pin.rstrip("/") for pin in pins or []]
    allowed = [gene.rstrip("/") for gene in mutable_genes]
    problems: list[str] = []
    for index, op in enumerate(patch.ops):
        gene = op_gene_path(op)
        if any(overlaps(pin, gene) for pin in pinned):
            problems.append(f"op {index} ({op.op}) touches pinned gene {gene!r}")
        elif not any(covers(rule, gene) for rule in allowed):
            problems.append(f"op {index} ({op.op}) touches {gene!r}, outside allowed_genes")
    return problems


def registration_from_json(payload: dict[str, Any]) -> StrategyRegistration:
    """Parse the author-written record, raising ``ValueError`` on a malformed shape."""

    runtime_payload = payload.get("runtime") or {}
    try:
        runtime = Runtime(
            kind=RuntimeKind(runtime_payload.get("kind", "")),
            image=runtime_payload.get("image"),
            package=runtime_payload.get("package"),
        )
    except ValueError as exc:
        raise ValueError(f"runtime.kind is not in_process or job_worker: {exc}") from exc

    needs = payload.get("needs") or {}
    if not isinstance(needs, dict):
        raise ValueError("needs must be an object of 'name@version' -> arguments")  # noqa: TRY004 - preserve public ValueError validation contract
    for key, arguments in needs.items():
        if not isinstance(arguments, dict):
            raise ValueError(f"needs[{key!r}] must be an object")  # noqa: TRY004 - preserve public ValueError validation contract

    params_schema = payload.get("params_schema")
    if params_schema is not None and not isinstance(params_schema, dict):
        raise ValueError("params_schema must be an object when present")

    return StrategyRegistration(
        id=str(payload.get("id", "")),
        version=str(payload.get("version", "")),
        runtime=runtime,
        needs=needs,
        params_schema=params_schema,
    )
