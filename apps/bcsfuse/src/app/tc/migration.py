"""Streaming offline migration of TC base identity into one current Card.

CSV is a base-information source, not a substitute for the authoritative
runtime/config/profile stores. Historical prose is accepted only for archival.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from search_core.api.cards import CardRecord

from app.domain.models.worker_config import WorkerConfig
from app.tc.catalog import TcCatalog


class MigrationRepository(Protocol):
    """The public SQL catalog capabilities used by the offline consumer."""

    environment: str

    def read_card(self, agent_id: str) -> CardRecord | None: ...

    def upsert(
        self,
        agent_id: str,
        fields: dict[str, Any],
        resource_type: str,
        search_config: dict[str, Any],
        *,
        labels: dict[str, list[str]] | None = None,
    ) -> CardRecord: ...

    def save_config(
        self, agent_id: str, expected_revision: int, config: dict[str, Any]
    ) -> bool: ...

    def remove(self, agent_id: str) -> None: ...


@dataclass
class MigrationReport:
    dry_run: bool
    counts: Counter = field(default_factory=lambda: Counter(total=0))
    reasons: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "scope": "CSV base information; other authoritative stores require reconciliation",
            "counts": dict(self.counts),
            "reasons": dict(self.reasons),
        }


class MigrationWriteError(RuntimeError):
    """A partial run failed; the safe report describes committed predecessors."""

    def __init__(self, row_number: int, report: MigrationReport):
        super().__init__(
            f"Migration failed at row {row_number}; rerun after resolving the write failure"
        )
        self.report = report


class _InvalidRow(ValueError):
    pass


def _text(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidRow(reason)
    return value


def _bot_info(value: Any) -> dict:
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise _InvalidRow("invalid_bot_info") from None
    if not isinstance(value, dict):
        raise _InvalidRow("invalid_bot_info")
    return value


def _skills(value: Any) -> list[dict]:
    if not isinstance(value, list):
        raise _InvalidRow("invalid_skills")
    result = []
    for skill in value:
        if not isinstance(skill, dict):
            raise _InvalidRow("invalid_skills")
        name = _text(skill.get("name"), "invalid_skills")
        description = skill.get("description")
        if description is not None and not isinstance(description, str):
            raise _InvalidRow("invalid_skills")
        result.append({"name": name, "description": description})
    return result


def _legacy(value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _InvalidRow("invalid_legacy_record")
    allowed = {
        "profile_id",
        "runtime_state",
        "trust_level",
        "lifecycle_state",
        "config",
        "archive",
    }
    if set(value) - allowed:
        raise _InvalidRow("unknown_legacy_field")
    for key in ("profile_id", "runtime_state", "trust_level", "lifecycle_state"):
        if key in value:
            _text(value[key], f"invalid_{key}")
    for key in ("config", "archive"):
        if key in value and not isinstance(value[key], dict):
            raise _InvalidRow(f"invalid_{key}")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise _InvalidRow("invalid_legacy_record") from None
    return dict(value)


def _fields(
    row: Mapping, current: CardRecord | None, legacy: dict, environment: str
) -> dict:
    info = _bot_info(row.get("bot_info"))
    description = info.get("summary")
    if description is not None and not isinstance(description, str):
        raise _InvalidRow("invalid_description")
    domains = info.get("domains", [])
    if not isinstance(domains, list) or any(
        not isinstance(item, str) for item in domains
    ):
        raise _InvalidRow("invalid_domains")
    kind = row.get("actor_kind")
    if kind not in {"bot", "human"}:
        raise _InvalidRow("invalid_type")
    availability = row.get("visibility")
    if availability not in {"private", "protected", "public"}:
        raise _InvalidRow("invalid_availability")
    fields = {
        "name": _text(info.get("name", row.get("name")), "invalid_name"),
        "description": description,
        "skills": _skills(info.get("skills", [])),
        "domains": domains,
        "environment": environment,
        "type": kind,
        "availability": availability,
    }
    existing = current.card.source_card if current else {}
    defaults = {
        "runtime_state": row.get("status") or "offline",
        "profile_id": "default",
        "trust_level": "guarded",
        "lifecycle_state": "active",
    }
    for key, default in defaults.items():
        fields[key] = _text(
            legacy.get(key, existing.get(key, default)), f"invalid_{key}"
        )
    for key in (
        "agent_code",
        "provider_id",
        "provider_bot_ref",
        "created_by",
        "connection_mode",
    ):
        if row.get(key):
            fields[key] = _text(row[key], f"invalid_{key}")
    try:
        TcCatalog.validate({**existing, **fields})
    except (ValueError, TypeError):
        raise _InvalidRow("invalid_card") from None
    return fields


def _config(current: CardRecord | None, legacy: dict) -> dict | None:
    existing = current.config if current else None
    if existing is not None and not isinstance(existing, dict):
        raise _InvalidRow("invalid_config")
    try:
        values = {**(existing or {}), **legacy.get("config", {})}
        normalized = WorkerConfig.model_validate(values).model_dump(mode="json")
        json.dumps(normalized, allow_nan=False)
    except (ValueError, TypeError):
        raise _InvalidRow("invalid_config") from None
    # Missing configuration remains missing; domain reads already default fusion off.
    return normalized if existing is not None or "config" in legacy else None


def _migrate_row(
    row: Mapping,
    repository: MigrationRepository | None,
    environment: str,
    legacy_lookup: Callable[[str, str], Mapping | None] | None,
    dry_run: bool,
) -> tuple[str, dict, dict]:
    source_environment = _text(row.get("env"), "invalid_environment")
    if source_environment != environment:
        return "skipped_environment", {}, {}
    worker_id = _text(row.get("bot_uuid"), "invalid_worker_id")
    deleted = row.get("is_deleted", "0")
    if deleted not in ("0", "1", 0, 1, False, True):
        raise _InvalidRow("invalid_deleted")
    current = repository.read_card(worker_id) if repository else None
    old = legacy_lookup(environment, worker_id) if legacy_lookup else None
    historical = {}
    if old is not None:
        if not isinstance(old, Mapping) or not isinstance(old.get("archive", {}), dict):
            raise _InvalidRow("invalid_archive")
        historical = old.get("archive", {})
    if deleted in ("1", 1, True):
        if not dry_run:
            repository.remove(worker_id)
        return "deleted", {}, historical
    if current and current.card.deleted:
        return "skipped_tombstone", {}, historical
    legacy = _legacy(old)
    fields = _fields(row, current, legacy, environment)
    labels = {"visibility": [fields["availability"]]}
    if row.get("user_visibility"):
        if row["user_visibility"] not in {"private", "protected", "public"}:
            raise _InvalidRow("invalid_user_visibility")
        labels["user_visibility"] = [row["user_visibility"]]
    config = _config(current, legacy)
    if not dry_run:
        record = repository.upsert(
            worker_id, fields, resource_type="agent", search_config={}, labels=labels
        )
        if (
            config is not None
            and config != (record.config or {})
            and not repository.save_config(
                worker_id,
                record.revision,
                config,
            )
        ):
            raise RuntimeError("configuration revision conflict")
    return "migrated", fields, legacy.get("archive", {})


def migrate_rows(
    rows: Iterable[Mapping[str, Any]],
    repository: MigrationRepository | None,
    *,
    environment: str,
    legacy_lookup: Callable[[str, str], Mapping | None] | None = None,
    archive: Callable[[dict[str, Any]], None] | None = None,
    dry_run: bool = True,
) -> MigrationReport:
    """Process one row at a time in a single explicitly selected environment.

    ``legacy_lookup`` returns selected profile identity, authoritative state/config
    and historical material under ``archive``. Missing values preserve an existing
    Card. ``archive`` receives safe identity/projection/hash records; source CSV
    remains the original evidence. Persistence failures abort with a safe report.
    """
    _text(environment, "invalid_environment")
    if not dry_run and repository is None:
        raise ValueError("writing requires an injected repository")
    if repository is not None and repository.environment != environment:
        raise ValueError("repository environment does not match selected environment")
    report = MigrationReport(dry_run)
    for number, row in enumerate(rows, 1):
        report.counts["total"] += 1
        try:
            outcome, fields, historical = _migrate_row(
                row,
                repository,
                environment,
                legacy_lookup,
                dry_run,
            )
        except _InvalidRow as error:
            outcome, fields, historical = "invalid", {}, {}
            report.reasons[str(error)] += 1
        except Exception:  # noqa: BLE001 - rethrow all provider errors without sensitive diagnostics
            report.counts["failed"] += 1
            report.reasons["persistence_failed"] += 1
            raise MigrationWriteError(number, report) from None
        if archive:
            try:
                encoded = json.dumps(
                    dict(row), sort_keys=True, ensure_ascii=False, allow_nan=False
                )
                archive(
                    {
                        "row_number": number,
                        "environment": row.get("env"),
                        "worker_id": row.get("bot_uuid"),
                        "outcome": outcome,
                        "source_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                        "source_card": fields,
                        "legacy_archive": historical,
                    }
                )
            except Exception:  # noqa: BLE001 - archive failures must abort without disclosing data
                report.counts["failed"] += 1
                report.reasons["archive_failed"] += 1
                raise MigrationWriteError(number, report) from None
        report.counts[outcome] += 1
    return report
