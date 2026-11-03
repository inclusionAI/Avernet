"""Additive management tables; settings and rule snapshot commit atomically."""

import sqlite3
from pathlib import Path

from engine.community.kernel.dws_hosting import Binding
from engine.community.kernel.dws_management import HostingSettings, SettingsUpdate
from engine.community.plugins.dws_hosting.database import HostingDatabase


class SqliteHostingManagementStore:
    def __init__(self, state_dir: Path):
        self.db = HostingDatabase(state_dir)
        with self.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS hosting_settings (binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id), settings_json TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS hosting_request_nonces (nonce TEXT PRIMARY KEY, created_ms INTEGER NOT NULL)"
            )

    @staticmethod
    def _binding(db, binding):
        row = db.execute(
            "SELECT * FROM bindings WHERE binding_id=?", (binding.binding_id,)
        ).fetchone()
        if row is None or Binding.model_validate_json(row["identity_json"]) != binding:
            raise PermissionError("binding_mismatch")
        return row

    def get(self, binding):
        with self.db.transaction() as db:
            row = self._binding(db, binding)
            settings = db.execute(
                "SELECT settings_json FROM hosting_settings WHERE binding_id=?",
                (binding.binding_id,),
            ).fetchone()
            return {
                "version": row["rule_version"],
                "settings": HostingSettings.model_validate_json(
                    settings[0]
                ).model_dump()
                if settings
                else HostingSettings().model_dump(),
            }

    def save(self, binding, update: SettingsUpdate):
        if not update.settings.rules.strip():
            raise ValueError("rules_required")
        with self.db.transaction() as db:
            row = self._binding(db, binding)
            if row["rule_version"] != update.expected_version:
                raise ValueError("version_conflict")
            db.execute(
                "INSERT INTO hosting_settings VALUES (?,?) ON CONFLICT(binding_id) DO UPDATE SET settings_json=excluded.settings_json",
                (binding.binding_id, update.settings.model_dump_json()),
            )
            db.execute(
                "UPDATE bindings SET rule_version=rule_version+1,rule_text=? WHERE binding_id=?",
                (update.settings.instructions(), binding.binding_id),
            )
            return {
                "version": row["rule_version"] + 1,
                "settings": update.settings.model_dump(),
            }

    def conversations(self, binding, *, offset, limit):
        with self.db.transaction() as db:
            self._binding(db, binding)
            total = db.execute(
                "SELECT count(*) FROM conversations WHERE binding_id=?",
                (binding.binding_id,),
            ).fetchone()[0]
            rows = db.execute(
                "SELECT conversation_id,taken_over FROM conversations WHERE binding_id=? ORDER BY conversation_id LIMIT ? OFFSET ?",
                (binding.binding_id, limit, offset),
            ).fetchall()
            return {
                "total": total,
                "items": [
                    {"conversation_id": r[0], "taken_over": bool(r[1])} for r in rows
                ],
            }

    def consume_nonce(self, nonce, now_ms):
        with self.db.transaction() as db:
            db.execute(
                "DELETE FROM hosting_request_nonces WHERE created_ms < ?",
                (now_ms - 120000,),
            )
            if (
                db.execute("SELECT count(*) FROM hosting_request_nonces").fetchone()[0]
                >= 10000
            ):
                raise ValueError("request_capacity")
            try:
                db.execute(
                    "INSERT INTO hosting_request_nonces VALUES (?,?)", (nonce, now_ms)
                )
            except sqlite3.IntegrityError:
                raise PermissionError("request_replayed") from None
