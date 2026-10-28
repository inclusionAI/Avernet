"""Legacy SkillSet defaults behavior."""

from typing import Any, Mapping, Optional

from agentclaw.community.core.skill_center.policies.default_skill_set_selection import (
    DefaultSkillSetSelection,
)

from .skill_set_context import (
    logger,
)


class SkillSetDefaultContext:
    def _default_skill_set_selection(
        self, engine_type: str | None = None
    ) -> DefaultSkillSetSelection:
        return self._default_skill_set_selection_candidates(engine_type)[0]

    def _default_skill_set_selection_candidates(
        self, engine_type: str | None = None, bolt_id: str | None = None
    ) -> tuple[DefaultSkillSetSelection, ...]:
        return self._default_skill_set_selection_policy.resolve_candidates(
            persisted_engine_type=self.engine_type
            if engine_type is None
            else engine_type,
            runtime_engine_type=self.runtime_engine_type,
            bolt_id=bolt_id,
        )

    def _default_skill_set_query_kwargs(
        self,
        engine_type: str | None = None,
        selection: DefaultSkillSetSelection | None = None,
    ) -> dict[str, str | None]:
        """Return repository kwargs only when compatibility needs them.

        OpenClaw and ordinary Claude Code keep the exact historical query shape.
        Routed Claude Code can provide ordered global default lookup candidates
        through the default SkillSet selection policy.
        """
        persisted_engine = self.engine_type if engine_type is None else engine_type
        effective_selection = selection or self._default_skill_set_selection(
            persisted_engine
        )
        if (
            effective_selection.bolt_id is None
            and effective_selection.engine_type == persisted_engine
        ):
            return {}
        return {
            "default_skill_set_bolt_id": effective_selection.bolt_id,
            "default_skill_set_engine_type": effective_selection.engine_type,
        }

    @staticmethod
    def _has_default_skill_set(skill_sets: list[dict]) -> bool:
        return any(skill_set.get("is_default") for skill_set in skill_sets)

    def _ordered_active_default_selections(
        self, *, bolt_id: str | None, engine_type: str | None
    ) -> tuple[DefaultSkillSetSelection, ...] | None:
        candidates = self._default_skill_set_selection_candidates(
            engine_type, bolt_id=bolt_id
        )
        if (
            len(candidates) == 1
            and candidates[0].bolt_id is None
            and candidates[0].engine_type == engine_type
        ):
            return None

        # Compatibility-only path. Keep the generic repository query primitive
        # unchanged, and let engine-specific resolvers contribute the lookup
        # order (for example: bot-scoped default -> global fallbacks).
        return candidates

    def _get_all_active_skill_sets_with_default_fallback(
        self,
        *,
        user_id: str | None,
        bolt_id: str | None,
        engine_type: str | None,
    ) -> list[dict]:
        selections = self._ordered_active_default_selections(
            bolt_id=bolt_id, engine_type=engine_type
        )
        if selections is None:
            return self.skill_set_repo.get_all_active_skill_sets(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
            )

        first_result: list[dict] | None = None
        for selection in selections:
            result = self.skill_set_repo.get_all_active_skill_sets(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
                **self._default_skill_set_query_kwargs(engine_type, selection),
            )
            if first_result is None:
                first_result = result
            if self._has_default_skill_set(result):
                return result
        return first_result or []

    def _get_all_active_skill_sets_for_env_with_default_fallback(
        self,
        *,
        user_id: str | None,
        bolt_id: str | None,
        engine_type: str | None,
        env: str,
    ) -> list[dict]:
        selections = self._ordered_active_default_selections(
            bolt_id=bolt_id, engine_type=engine_type
        )
        if selections is None:
            return self.skill_set_repo.get_all_active_skill_sets_for_env(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
                env=env,
            )

        first_result: list[dict] | None = None
        for selection in selections:
            result = self.skill_set_repo.get_all_active_skill_sets_for_env(
                user_id=user_id,
                bolt_id=bolt_id,
                engine_type=engine_type,
                env=env,
                **self._default_skill_set_query_kwargs(engine_type, selection),
            )
            if first_result is None:
                first_result = result
            if self._has_default_skill_set(result):
                return result
        return first_result or []

    def _get_default_capabilities_ext_info(
        self,
        engine_type: Optional[str],
        bot_id: Optional[str] = None,
        *,
        strict: bool = False,
    ) -> Mapping[str, Any] | None:
        """Resolve extra context for engine-specific default capabilities.

        Display/read callers keep their historical best-effort behavior.
        Runtime projection passes ``strict=True`` because a dependency failure
        must not be interpreted as removal of template-only Default MCPs.
        """
        provider = self._ext_info_provider
        if provider is None:
            # Runtime DI always supplies this provider. In strict mode a
            # missing provider means the service was constructed incorrectly;
            # it is not evidence that this Bot has no template defaults.
            if strict:
                raise RuntimeError("Default MCP policy provider is unavailable")
            return None
        target_bot_id = bot_id or self.bot_id
        if not target_bot_id:
            return None
        if strict:
            ext_info = provider(str(target_bot_id))
        else:
            try:
                ext_info = provider(str(target_bot_id))
            except Exception as exc:
                logger.warning(
                    "[SkillSetService] default capabilities ext_info lookup failed for "
                    "engine_type=%s bot_id=%s: %s",
                    engine_type,
                    target_bot_id,
                    exc,
                )
                return None
        return ext_info if isinstance(ext_info, Mapping) else None

    def _get_default_capabilities_template_type(
        self,
        bot_id: Optional[str] = None,
        *,
        entity_id: Optional[str] = None,
        strict: bool = False,
    ) -> str | None:
        """Resolve template routing context for Default capabilities.

        Runtime projection uses strict mode for the same reason as ext info:
        a repository failure is not evidence that the Bot has no template.
        """
        target_bot_id = bot_id or self.bot_id
        target_entity_id = entity_id or self.entity_id
        if not target_bot_id or not target_entity_id:
            if strict:
                raise RuntimeError("Default MCP template context is unavailable")
            return None
        try:
            bot = self._bot_repo.get_by_id_and_entity(
                str(target_bot_id), str(target_entity_id)
            )
        except Exception as exc:
            if strict:
                raise
            logger.warning(
                "[SkillSetService] default capabilities template_type lookup failed for "
                "bot_id=%s: %s",
                target_bot_id,
                exc,
            )
            return None
        if not isinstance(bot, dict):
            if strict:
                raise RuntimeError("Default MCP template context is unavailable")
            return None
        template_type = bot.get("template_type")
        return template_type if isinstance(template_type, str) else None
