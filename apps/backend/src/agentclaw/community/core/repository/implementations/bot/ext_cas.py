"""Owner/env-scoped atomic Bot extension and lifecycle transitions."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from sqlalchemy import func


class BotExtCompareAndSet:
    def compare_and_set_ext(
        self,
        *,
        bot_id: str,
        owner_id: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
        status: str | None = None,
        expected_state: Dict[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        """CAS ext, optionally transitioning status in the same guarded write."""
        expected_json = json.dumps(expected_ext) if expected_ext is not None else None
        ext_json = json.dumps(ext)
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(),
            )
            for field, value in (expected_state or {}).items():
                if field not in {"status", "binding_id", "active_engine"}:
                    raise ValueError(f"Unsupported lifecycle fence: {field}")
                query = query.filter(getattr(self.Model, field) == value)
            if expected_json is None:
                query = query.filter(self.Model.ext.is_(None))
            else:
                query = query.filter(self.Model.ext == expected_json)
            values = {self.Model.ext: ext_json, self.Model.gmt_modified: func.now()}
            if status is not None:
                values[self.Model.status] = status
            affected = query.update(values, synchronize_session=False)
        if affected == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id)
