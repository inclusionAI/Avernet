"""Freeze a service draft's Skills layout declaration for one publish version.

This module deliberately owns only the service-publish contract.  It does not
resolve the rollout whitelist or engine-specific filesystem paths.  The
editable draft's persisted active layout is the sole input; the engine build
provider remains responsible for the physical versioned snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.utils.env_utils import get_current_env


@dataclass(frozen=True, slots=True)
class CapturedServiceSkillsLayout:
    """The draft layout decision captured before physical snapshotting starts."""

    engine: str
    scope: BotSkillLayoutScope
    active_layout: SkillLayout
    phase: SkillLayoutPhase
    migration_generation: str | None
    layout_contract_version: str | None


_SERVICE_MANIFEST_ENGINES = frozenset({"openclaw", "claude_code"})
SERVICE_SKILLS_POOL_CONTRACT_VERSION = "skills-pool-p3-v1"


class ServiceSkillsManifestError(RuntimeError):
    """The draft cannot be represented as a supported Skills manifest."""


class ServiceSkillsManifestBuilder:
    """Build the Skills manifest embedded in one versioned service artifact."""

    def __init__(
        self,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._layout_repository = layout_repository

    def capture(
        self,
        *,
        bot: dict[str, Any],
    ) -> CapturedServiceSkillsLayout | None:
        engine = str(bot.get("active_engine") or "openclaw").strip().lower()
        scope = BotSkillLayoutScope(
            env=str(bot.get("env") or get_current_env()),
            entity_id=str(bot.get("entity_id") or bot.get("owner_id") or ""),
            bot_id=str(bot.get("bot_id") or ""),
        )
        state = self._layout_repository.get(scope)

        if state.phase not in {
            SkillLayoutPhase.LEGACY_ACTIVE,
            SkillLayoutPhase.POOL_ACTIVE,
        }:
            raise ServiceSkillsManifestError(
                "service build requires a terminal Skills layout state"
            )
        if (
            state.phase is SkillLayoutPhase.LEGACY_ACTIVE
            and state.active_layout is not SkillLayout.LEGACY
        ) or (
            state.phase is SkillLayoutPhase.POOL_ACTIVE
            and state.active_layout is not SkillLayout.POOL
        ):
            raise ServiceSkillsManifestError(
                "service build found an inconsistent terminal Skills layout"
            )

        if engine == "aicoding":
            raise ServiceSkillsManifestError(
                "AICoding service publishing is not supported"
            )
        if engine == "hermes":
            if state.active_layout is SkillLayout.POOL:
                raise ServiceSkillsManifestError(
                    "Hermes Pool service manifest is disabled until native "
                    "service delivery is verified"
                )
            # Preserve the pre-Pool Legacy service path.  It has no verified
            # native Hermes manifest contract, so do not falsely stamp one.
            return None

        if engine not in _SERVICE_MANIFEST_ENGINES:
            raise ServiceSkillsManifestError(
                f"service Skills manifest is not supported for engine: {engine}"
            )

        is_pool = state.active_layout is SkillLayout.POOL
        if is_pool and (
            not state.persisted
            or state.phase is not SkillLayoutPhase.POOL_ACTIVE
            or not state.layout_contract_version
        ):
            raise ServiceSkillsManifestError(
                "Pool service manifest requires a persisted POOL_ACTIVE draft"
            )

        return CapturedServiceSkillsLayout(
            engine=engine,
            scope=scope,
            active_layout=state.active_layout,
            phase=state.phase,
            migration_generation=state.migration_generation,
            # Capture the raw persisted value even for Legacy.  Legacy emits no
            # Pool contract, but a concurrent writer changing this field still
            # means the layout state moved while the physical snapshot ran.
            layout_contract_version=state.layout_contract_version,
        )

    def finalize(
        self,
        *,
        captured: CapturedServiceSkillsLayout,
    ) -> dict[str, Any]:
        engine = captured.engine
        current = self._layout_repository.get(captured.scope)
        if (
            current.active_layout is not captured.active_layout
            or current.phase is not captured.phase
            or current.migration_generation != captured.migration_generation
            or current.layout_contract_version
            != captured.layout_contract_version
        ):
            raise ServiceSkillsManifestError(
                "draft Skills layout changed during service build"
            )

        return {
            "schema_version": 1,
            "engine": engine,
            "active_layout": captured.active_layout.value,
            "layout_contract_version": (
                captured.layout_contract_version
                if captured.active_layout is SkillLayout.POOL
                else None
            ),
        }


def validate_service_skills_manifest_for_release(
    manifest: dict[str, Any],
    bot: dict[str, Any],
) -> None:
    """Fail closed when a live draft identity no longer matches its manifest."""

    if manifest.get("schema_version") != 1:
        raise ServiceSkillsManifestError(
            "unsupported service Skills manifest schema"
        )
    manifest_engine = str(manifest.get("engine") or "").strip().lower()
    live_engine = str(bot.get("active_engine") or "openclaw").strip().lower()
    if manifest_engine != live_engine:
        raise ServiceSkillsManifestError(
            "live Bot engine no longer matches the frozen service Skills manifest"
        )
    active_layout = manifest.get("active_layout")
    if active_layout not in {SkillLayout.LEGACY.value, SkillLayout.POOL.value}:
        raise ServiceSkillsManifestError(
            "invalid active layout in service Skills manifest"
        )
    if (
        active_layout == SkillLayout.POOL.value
        and manifest.get("layout_contract_version")
        != SERVICE_SKILLS_POOL_CONTRACT_VERSION
    ):
        raise ServiceSkillsManifestError(
            "Pool service Skills manifest uses an unsupported layout contract"
        )


def service_skills_manifest_env(
    manifest: dict[str, Any],
    bot: dict[str, Any],
) -> dict[str, str]:
    """Translate the frozen manifest into the backwards-compatible wire contract."""

    validate_service_skills_manifest_for_release(manifest, bot)
    env = {
        "AGENTCLAW_SKILLS_LAYOUT": str(manifest["active_layout"]),
    }
    contract = manifest.get("layout_contract_version")
    if contract:
        env["AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION"] = str(contract)
    return env


def service_skills_env_from_ext(
    ext: dict[str, Any] | None,
    bot: dict[str, Any],
) -> dict[str, str]:
    """Return the immutable layout declaration, defaulting old versions to Legacy."""

    manifest = (ext or {}).get("skills_manifest")
    if manifest is None:
        return {"AGENTCLAW_SKILLS_LAYOUT": SkillLayout.LEGACY.value}
    return service_skills_manifest_env(manifest, bot)


__all__ = [
    "CapturedServiceSkillsLayout",
    "SERVICE_SKILLS_POOL_CONTRACT_VERSION",
    "ServiceSkillsManifestBuilder",
    "ServiceSkillsManifestError",
    "service_skills_manifest_env",
    "service_skills_env_from_ext",
    "validate_service_skills_manifest_for_release",
]
