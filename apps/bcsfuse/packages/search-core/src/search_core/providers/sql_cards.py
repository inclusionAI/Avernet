"""Card snapshots and derived business fields; vectors live only in Doc storage."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Literal

from search_core.api.cards import (
    CardRecord,
    CatalogConflict,
)
from search_core.providers.card_schema import validate_card_table
from search_core.providers.card_writes import CardWrites
from search_core.providers.sql_index_documents import (
    validate_environment,
)

_COLUMNS = "card_json, revision, prepared_revision, error_code, profile_json, config, resource_type, is_deleted, preparation_key, labels"


class SqlCardRepository(CardWrites):
    """DB-API adapter. Internal tables are provisioned by DDL, never at startup."""

    def __init__(
        self,
        connect: Callable[[], Any],
        *,
        table: str,
        environment: str = "local",
        placeholder: Literal["?", "%s"] = "%s",
    ):
        validate_card_table(table)
        if placeholder not in {"?", "%s"}:
            raise ValueError("invalid SQL placeholder")
        self.connect, self.table, self.placeholder = connect, table, placeholder
        self.environment = validate_environment(environment)
        self.now_expression = (
            "(julianday('now') - 2440587.5) * 86400.0"
            if placeholder == "?"
            else "CURRENT_TIMESTAMP(6)"
        )

    @property
    def _next_modified(self):
        increment = (
            "gmt_modify+0.000001"
            if self.placeholder == "?"
            else "TIMESTAMPADD(MICROSECOND,1,gmt_modify)"
        )
        return f"CASE WHEN gmt_modify>={self.now_expression} THEN {increment} ELSE {self.now_expression} END"

    @contextmanager
    def _cursor(self):
        conn = self.connect()
        try:
            cursor = conn.cursor()
            try:
                yield conn, cursor
            finally:
                cursor.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _execute(self, cursor, sql, params=()):
        return cursor.execute(sql.replace("?", self.placeholder), params)

    @classmethod
    def _decode(cls, row) -> CardRecord | None:
        if row is None:
            return None
        if isinstance(row, dict):
            row = tuple(row[k] for k in _COLUMNS.split(", "))
        return CardRecord(
            cls._card_with_labels(row[0], row[6], bool(row[7]), row[9]),
            row[1],
            row[2],
            None,
            row[3],
            json.loads(row[4]) if row[4] is not None else None,
            json.loads(row[5]) if row[5] is not None else None,
            preparation_key=row[8],
        )

    @classmethod
    def _card(cls, encoded, resource_type, deleted):
        data = json.loads(encoded)
        data["resource_type"] = resource_type
        data["deleted"] = deleted
        return cls.snapshot_type.model_validate(data)

    @classmethod
    def _card_with_labels(cls, encoded, kind, deleted, labels):
        card = cls._card(encoded, kind, deleted)
        if not labels:
            return card
        return card.model_copy(
            update={
                "labels": json.loads(labels)
                if isinstance(labels, (str, bytes))
                else labels
            }
        )

    def read_card(self, agent_id):
        """Read business data and source metadata without loading index artifacts."""
        with self._cursor() as (_, cur):
            self._execute(
                cur,
                f"SELECT {_COLUMNS} FROM {self.table} WHERE environment=? AND agent_id=?",
                (self.environment, agent_id),
            )
            row = self._decode(cur.fetchone())
        return row

    def get(self, agent_id):
        return self.read_card(agent_id)

    def get_many_cards(self, agent_ids):
        """Search enrichment reads only business Cards, never persisted vectors."""
        ids = tuple(dict.fromkeys(agent_ids))
        if not ids:
            return {}
        if len(ids) > 100:
            raise ValueError("at most 100 Cards per batch")
        with self._cursor() as (_, cur):
            marks = ",".join("?" for _ in ids)
            self._execute(
                cur,
                f"SELECT agent_id,card_json,resource_type,is_deleted,labels FROM {self.table} WHERE environment=? AND agent_id IN ({marks})",
                (self.environment, *ids),
            )
            rows = cur.fetchall()
        return {
            (
                row["agent_id"] if isinstance(row, dict) else row[0]
            ): self._card_with_labels(
                row["card_json"] if isinstance(row, dict) else row[1],
                row["resource_type"] if isinstance(row, dict) else row[2],
                bool(row["is_deleted"] if isinstance(row, dict) else row[3]),
                row["labels"] if isinstance(row, dict) else row[4],
            )
            for row in rows
        }

    def page(self, after, limit):
        return [(key, revision) for key, revision, _ in self.page_states(after, limit)]

    def page_states(self, after, limit):
        """Lightweight source versions with operational deletion, including tombstones."""
        if not 1 <= limit <= 1000:
            raise ValueError("invalid page size")
        with self._cursor() as (_, cur):
            self._execute(
                cur,
                f"SELECT agent_id, revision, is_deleted FROM {self.table} WHERE environment=? AND agent_id>? ORDER BY agent_id LIMIT ?",
                (self.environment, after, limit),
            )
            return [
                (row["agent_id"], row["revision"], bool(row["is_deleted"]))
                if isinstance(row, dict)
                else (row[0], row[1], bool(row[2]))
                for row in cur.fetchall()
            ]

    def save(self, card, expected_revision):
        if (
            expected_revision < 0
            or len(card.agent_id) > 128
            or len(card.source_version) > 128
        ):
            raise ValueError("invalid revision or Card identifier length")
        encoded = card.model_dump_json(exclude={"deleted", "labels"})
        labels = json.dumps(card.labels, ensure_ascii=False)
        current = self.get(card.agent_id)
        if current is not None and current.card.resource_type != card.resource_type:
            raise CatalogConflict("resource type cannot change for an existing ID")
        if (
            current is not None
            and not current.card.deleted
            and current.card.source_version == card.source_version
        ):
            if current.card == card:
                return current  # exact replay, including a response lost after commit
            raise CatalogConflict("source version already has different contents")
        if (current.revision if current else 0) != expected_revision:
            raise CatalogConflict("revision conflict")
        try:
            with self._cursor() as (conn, cur):
                cur.execute(
                    "BEGIN IMMEDIATE"
                    if self.placeholder == "?"
                    else "START TRANSACTION"
                )
                if current is None:
                    self._execute(
                        cur,
                        f"INSERT INTO {self.table} (environment, agent_id, card_json, labels, resource_type, is_deleted, revision, prepared_revision, error_code, gmt_create, gmt_modify) VALUES (?, ?, ?, ?, ?, ?, 1, 0, '', {self.now_expression}, {self.now_expression})",
                        (
                            self.environment,
                            card.agent_id,
                            encoded,
                            labels,
                            card.resource_type,
                            int(card.deleted),
                        ),
                    )
                else:
                    self._execute(
                        cur,
                        f"UPDATE {self.table} SET card_json=?, labels=?, resource_type=?, is_deleted=?, revision=revision+1, error_code='', preparation_token='', lease_until=0, gmt_modify={self._next_modified} WHERE environment=? AND agent_id=? AND revision=?",
                        (
                            encoded,
                            labels,
                            card.resource_type,
                            int(card.deleted),
                            self.environment,
                            card.agent_id,
                            expected_revision,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise CatalogConflict("revision conflict")
                if card.deleted:
                    self._retire_documents(cur, card.agent_id, expected_revision + 1)
                conn.commit()
        except Exception as error:
            if current is None:
                # Resolve a competing create without hiding an actual DB failure.
                latest = self.get(card.agent_id)
                if latest is not None:
                    if latest.card == card:
                        return latest
                    raise CatalogConflict("concurrent Card creation") from error
            raise
        # Describe precisely this committed revision, even if a later write wins.
        return CardRecord(
            card,
            expected_revision + 1,
            current.prepared_revision if current else 0,
            current.prepared if current else None,
            "",
            current.profile if current else None,
            current.config if current else None,
        )

    def delete(self, agent_id, expected_revision):
        """Persist a versioned tombstone; outstanding preparation loses its lease.

        Retaining this internal record lets every replica observe removal and
        prevents a delayed PUT or DELETE from overwriting a recreated resource.
        """
        current = self.get(agent_id)
        if current is None:
            raise KeyError(agent_id)
        if current.card.deleted and expected_revision in {
            current.revision,
            current.revision - 1,
        }:
            return current
        if expected_revision != current.revision:
            raise CatalogConflict("revision conflict")
        deleted = current.card.model_copy(update={"deleted": True})
        with self._cursor() as (conn, cur):
            cur.execute(
                "BEGIN IMMEDIATE" if self.placeholder == "?" else "START TRANSACTION"
            )
            self._execute(
                cur,
                f"UPDATE {self.table} SET is_deleted=1, revision=revision+1, "
                "error_code='', preparation_token='', lease_until=0, "
                f"gmt_modify={self._next_modified} "
                "WHERE environment=? AND agent_id=? AND revision=?",
                (
                    self.environment,
                    agent_id,
                    expected_revision,
                ),
            )
            if cur.rowcount != 1:
                raise CatalogConflict("revision conflict")
            self._retire_documents(cur, agent_id, expected_revision + 1)
            conn.commit()
        return replace(
            current, card=deleted, revision=expected_revision + 1, error_code=""
        )

    def _retire_documents(self, cursor, agent_id, revision):
        """Source-only storage has no Docs; catalog composition overrides this hook."""

    def save_profile(self, agent_id, expected_revision, profile):
        """Store business processing output only for the source revision read.

        Profile is reserved for the TC adapter; the current Alipay converter
        consumes Card fields only. This does not acknowledge Doc publication.
        """
        if not isinstance(profile, dict) or expected_revision < 1:
            raise ValueError("profile must be an object with a valid source revision")
        encoded = json.dumps(
            {"source_revision": expected_revision, "content": profile},
            ensure_ascii=False,
            allow_nan=False,
        )
        with self._cursor() as (conn, cur):
            self._execute(
                cur,
                f"UPDATE {self.table} SET profile_json=?, gmt_modify=gmt_modify WHERE environment=? AND agent_id=? AND revision=?",
                (encoded, self.environment, agent_id, expected_revision),
            )
            changed = cur.rowcount == 1
            conn.commit()
            return changed

    def save_config(self, agent_id, expected_revision, config):
        """CAS business configuration; advance revision for sync and fencing."""
        if not isinstance(config, dict) or expected_revision < 1:
            raise ValueError("config must be an object with a valid source revision")
        encoded = json.dumps(config, ensure_ascii=False, allow_nan=False)
        with self._cursor() as (conn, cur):
            self._execute(
                cur,
                f"UPDATE {self.table} SET config=?, revision=revision+1, preparation_token='', lease_until=0, gmt_modify={self._next_modified} WHERE environment=? AND agent_id=? AND revision=?",
                (encoded, self.environment, agent_id, expected_revision),
            )
            changed = cur.rowcount == 1
            conn.commit()
            return changed

    def record_error(self, agent_id, revision, code):
        with self._cursor() as (conn, cur):
            self._execute(
                cur,
                f"UPDATE {self.table} SET error_code=?, gmt_modify=gmt_modify WHERE environment=? AND agent_id=? AND revision=?",
                (code, self.environment, agent_id, revision),
            )
            conn.commit()


def sqlite_card_repository(
    path: str, *, environment: str = "local"
) -> SqlCardRepository:
    """Local Card source table; index runtime and providers are composed separately."""
    if not path or path == ":memory:":
        raise ValueError("catalog requires a SQLite file")
    validate_environment(environment)

    def connect():
        return sqlite3.connect(path, timeout=10)

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        from search_core.providers.card_schema import initialize_sqlite_cards

        initialize_sqlite_cards(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return SqlCardRepository(
        connect, table="bcsfuse_agent_cards", environment=environment, placeholder="?"
    )
