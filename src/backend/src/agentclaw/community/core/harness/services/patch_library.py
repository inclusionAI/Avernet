"""PatchLibrary — patch template storage and indexing.

Manages PatchTemplate CRUD + in-memory indices (by_layer, by_target_file, by_skill_set).
Templates are persisted via HarnessTemplateRepository.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.harness.models import (
    Layer,
    PatchDefinition,
    PatchTemplate,
    PatchTemplateStatus,
)
from agentclaw.community.core.harness.repository_protocol import HarnessTemplateRepository


class PatchLibrary:
    """Patch template management center.

    All Kits share this library; each Kit filters by ``layer``.
    """

    def __init__(self, repo: HarnessTemplateRepository):
        self._repo = repo
        self._templates: list[PatchTemplate] = []
        self._index_by_layer: dict[Layer, list[int]] = {}
        self._index_by_target_file: dict[str, list[int]] = {}
        self._index_by_skill_set: dict[str, list[int]] = {}

    def load_all(self) -> None:
        """Load all templates from DB and rebuild indices."""
        self._templates = []
        self._index_by_layer = {}
        self._index_by_target_file = {}
        self._index_by_skill_set = {}

        try:
            rows = self._repo.load_all_active()
            for row in rows:
                self._add_template(row)
        except Exception:
            # DB may not yet have the table; graceful fallback
            pass

    def _add_template(self, tpl: PatchTemplate) -> None:
        idx = len(self._templates)
        self._templates.append(tpl)

        self._index_by_layer.setdefault(tpl.layer, []).append(idx)
        for f in tpl.target.files:
            self._index_by_target_file.setdefault(f, []).append(idx)

        cond = tpl.applicable_when or {}
        for sk in cond.get("skill_sets_contains_any", []):
            self._index_by_skill_set.setdefault(sk, []).append(idx)

    def get_template(self, key: str, env: str) -> PatchTemplate | None:
        """Fetch template by ID (if numeric) or name directly from DB.

        Args:
            key: Template ID (numeric string) or template name.
            env: Environment for name-based lookup.
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info("[PatchLibrary] get_template: key=%s, env=%s, repo_type=%s", key, env, type(self._repo).__name__)

        # Try ID lookup first (if key is numeric)
        try:
            tpl_id = int(key)
            logger.info("[PatchLibrary] get_template: trying ID lookup, tpl_id=%s", tpl_id)
            tpl = self._repo.get_by_id(tpl_id)
            logger.info("[PatchLibrary] get_template: ID lookup result=%s", tpl)
            if tpl is not None:
                return tpl
        except (ValueError, TypeError) as e:
            logger.info("[PatchLibrary] get_template: ID lookup skipped, key=%s, error=%s", key, e)
            pass

        # Fall back to name lookup
        logger.info("[PatchLibrary] get_template: falling back to name lookup, key=%s, env=%s", key, env)
        result = self._repo.get_by_name(key, env)
        logger.info("[PatchLibrary] get_template: name lookup result=%s", result)
        return result

    def list_applicable(
        self,
        bot_meta: dict[str, Any],
        layer: Layer | None = Layer.L1,
    ) -> list[PatchTemplate]:
        """Return templates applicable to the given bot."""
        if not self._templates:
            self.load_all()

        result: list[PatchTemplate] = []
        skill_sets: set[str] = set(bot_meta.get("skill_sets", []))

        for tpl in self._templates:
            if layer and tpl.layer != layer:
                continue
            if tpl.status != PatchTemplateStatus.ACTIVE:
                continue
            cond = tpl.applicable_when or {}
            required = set(cond.get("skill_sets_contains_any", []))
            if required and not (required & skill_sets):
                continue
            result.append(tpl)

        return result

    # ── CRUD ──────────────────────────────────────────────────

    def create_template(self, tpl: PatchTemplate) -> PatchTemplate:
        """Persist a new template to DB and refresh indices."""
        result = self._repo.create(tpl)
        self.load_all()
        return result

    def update_template(self, tpl_id: int, **kwargs) -> PatchTemplate | None:
        """Update fields of an existing template by ID."""
        result = self._repo.update(tpl_id, **kwargs)
        if result:
            self.load_all()
        return result

    def get_template_by_id(self, tpl_id: int) -> PatchTemplate | None:
        """Fetch single template by ID."""
        return self._repo.get_by_id(tpl_id)

    def list_templates(
        self,
        layer: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[PatchTemplate], int]:
        """Return paginated list + total count."""
        return self._repo.list(layer=layer, status=status, keyword=keyword, offset=offset, limit=limit)

    def delete_template(self, tpl_id: int) -> bool:
        """Soft-delete by setting status='deprecated'."""
        result = self._repo.soft_delete(tpl_id)
        if result:
            self.load_all()
        return result

    def validate(self, patch_def: PatchDefinition) -> list[str]:
        """Validate a patch definition and return list of error messages (empty if OK)."""
        errors: list[str] = []
        if not patch_def.name:
            errors.append("Patch name is required")
        if patch_def.scope not in {"all", "bot_id", "bot_id_list"}:
            errors.append(f"Invalid scope: {patch_def.scope}")
        return errors
