"""An in-memory Strategy Registry reference implementation (§9)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .catalog import DEFAULT_CATALOG, Catalog, Engine
from .conformance import KIT_VERSION, run_conformance
from .errors import StrategyNotFound, VersionExists
from .registration import (
    AgentDefinition,
    ConformanceState,
    ConformanceStatus,
    RegisteredStrategy,
    StrategyRegistration,
    check_engine,
    check_params,
    definition_digest,
    derive_engines,
    validate_registration,
)


class InMemoryStrategyRegistry:
    """Reference registry; production persistence belongs to the evolution service."""

    def __init__(self, *, catalog: Catalog = DEFAULT_CATALOG, kit_version: str = KIT_VERSION) -> None:
        self.catalog = catalog
        self.kit_version = kit_version
        self._records: dict[tuple[str, str], RegisteredStrategy] = {}

    async def register(
        self,
        registration: StrategyRegistration,
        definitions: dict[str, dict[str, bytes]],
        *,
        strategy_factory: Any | None = None,
        parent: Any | None = None,
    ) -> RegisteredStrategy:
        problems = validate_registration(registration, catalog=self.catalog, definitions=definitions)
        if problems:
            raise ValueError("; ".join(problems))

        key = (registration.id, registration.version)
        stored_definitions = tuple(
            AgentDefinition(
                strategy=registration.id,
                strategy_version=registration.version,
                name=name,
                engine=ref.engine,
                digest=definition_digest(definitions[name]),
                validated_by=f"agents@1/{ref.engine.value}",
                files=tuple(sorted(definitions[name].items())),
            )
            for name, ref in registration.definition_refs().items()
        )
        normalized = _with_definition_digests(registration, stored_definitions)

        if key in self._records:
            existing = self._records[key]
            if _fingerprint(existing.registration, existing.definitions) != _fingerprint(
                normalized, stored_definitions
            ):
                raise VersionExists(
                    f"{registration.id}@{registration.version} already exists with different content"
                )
            return existing

        record = RegisteredStrategy(
            registration=normalized,
            engines=derive_engines(registration),
            conformance=ConformanceStatus(ConformanceState.PENDING, self.kit_version),
            registered_at=datetime.now(UTC),
            definitions=stored_definitions,
        )
        self._records[key] = record

        if strategy_factory is not None and parent is not None:
            report = run_conformance(strategy_factory, registration, parent=parent, catalog=self.catalog)
            record = RegisteredStrategy(
                registration=record.registration,
                engines=record.engines,
                conformance=report.status,
                enabled=record.enabled,
                registered_at=record.registered_at,
                definitions=record.definitions,
            )
            self._records[key] = record
        return record

    async def get(self, strategy_id: str, version: str) -> RegisteredStrategy:
        try:
            return self._records[(strategy_id, version)]
        except KeyError as exc:
            raise StrategyNotFound(f"{strategy_id}@{version}") from exc

    async def list(
        self,
        *,
        engine: Engine | None = None,
        conformance: ConformanceState | None = None,
    ) -> list[RegisteredStrategy]:
        values = list(self._records.values())
        if engine is not None:
            values = [
                entry
                for entry in values
                if not check_engine(entry.registration, engine, catalog=self.catalog)
            ]
        if conformance is not None:
            values = [entry for entry in values if entry.conformance.status is conformance]
        return sorted(
            values,
            key=lambda entry: (entry.registration.id, entry.registration.version),
        )

    async def check_engine(self, strategy_id: str, version: str, engine: Engine) -> list[str]:
        record = await self.get(strategy_id, version)
        return check_engine(record.registration, engine, catalog=self.catalog)

    async def check_binding(
        self,
        strategy_id: str,
        version: str,
        engine: Engine,
        params: dict[str, Any] | None = None,
    ) -> list[str]:
        record = await self.get(strategy_id, version)
        return check_engine(record.registration, engine, catalog=self.catalog) + check_params(
            record.registration, params
        )

    async def agent_definition(self, strategy_id: str, version: str, name: str) -> AgentDefinition:
        record = await self.get(strategy_id, version)
        definition = record.definition(name)
        if definition is None:
            raise KeyError(f"unknown agent definition {name!r}")
        return definition

    async def set_enabled(self, strategy_id: str, enabled: bool, *, reason: str) -> None:
        matches = [key for key in self._records if key[0] == strategy_id]
        if not matches:
            raise StrategyNotFound(strategy_id)
        for key in matches:
            record = self._records[key]
            self._records[key] = RegisteredStrategy(
                registration=record.registration,
                engines=record.engines,
                conformance=record.conformance,
                enabled=enabled,
                registered_at=record.registered_at,
                definitions=record.definitions,
            )


def _with_definition_digests(
    registration: StrategyRegistration, definitions: tuple[AgentDefinition, ...]
) -> StrategyRegistration:
    needs = {key: dict(value) for key, value in registration.needs.items()}
    if definitions:
        agents = dict(needs.get("agents@1") or {})
        agents["definitions"] = {
            definition.name: {
                "engine": definition.engine.value,
                "path": registration.definition_refs()[definition.name].path,
                "digest": definition.digest,
            }
            for definition in definitions
        }
        needs["agents@1"] = agents
    return StrategyRegistration(
        registration.id,
        registration.version,
        registration.runtime,
        needs,
        registration.params_schema,
    )


def _fingerprint(registration: StrategyRegistration, definitions: tuple[AgentDefinition, ...]) -> tuple:
    return (
        registration.id,
        registration.version,
        registration.runtime,
        tuple(sorted((key, repr(value)) for key, value in registration.needs.items())),
        registration.params_schema and repr(registration.params_schema),
        tuple((definition.name, definition.engine, definition.digest) for definition in definitions),
    )


__all__ = ["InMemoryStrategyRegistry"]
