"""Freeze a service draft's Skills layout declaration for one publish version.

This module deliberately owns only the service-publish contract. It never
guesses an engine-specific filesystem path: exact shared-corpus delivery is
strictly parsed from the versioned Engine Runtime probe evidence persisted with
the editable draft's active layout, then frozen into the historical artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeNamePolicy
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
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
    runtime_engine: str
    scope: BotSkillLayoutScope
    active_layout: SkillLayout
    phase: SkillLayoutPhase
    migration_generation: str | None
    layout_contract_version: str | None
    center_skills: tuple[dict[str, Any], ...]
    shared_corpora: tuple[ResolvedSharedCorpusDelivery, ...]


_SERVICE_MANIFEST_ENGINES = frozenset(
    {"openclaw", "claude_code", "aicoding", "hermes"}
)
SERVICE_SKILLS_POOL_CONTRACT_VERSION = "skills-pool-p3-v1"


class ServiceSkillsManifestError(RuntimeError):
    """The draft cannot be represented as a supported Skills manifest."""


@dataclass(frozen=True, slots=True)
class ResolvedSharedCorpusDelivery:
    """Strict, frozen delivery facts resolved by the Engine Runtime probe."""

    corpus: str
    runtime_path: str
    store_prefix: str
    layout_contract_version: str
    permission: str = "read_only"
    snapshot_policy: str = "exclude"

    @classmethod
    def center_from_state(
        cls,
        *,
        state,
        bot: dict[str, Any],
        store_prefix: str,
    ) -> ResolvedSharedCorpusDelivery:
        evidence = state.last_probe_evidence
        resolved = evidence.get("resolved_layout") if isinstance(evidence, dict) else None
        expected_engine = runtime_layout_engine_for_bot(bot)
        if (
            state.last_probe_result != "READY"
            or state.layout_contract_version != SERVICE_SKILLS_POOL_CONTRACT_VERSION
            or not isinstance(resolved, dict)
            or resolved.get("engine") != expected_engine
            or resolved.get("layout_contract_version")
            != SERVICE_SKILLS_POOL_CONTRACT_VERSION
        ):
            raise ServiceSkillsManifestError(
                "Center service build requires matching READY Engine layout evidence"
            )
        runtime_path = resolved.get("pool_center")
        if not isinstance(runtime_path, str):
            raise ServiceSkillsManifestError(
                "Center Engine layout evidence is missing pool_center"
            )
        path = PurePosixPath(runtime_path)
        if (
            not path.is_absolute()
            or runtime_path != path.as_posix()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ServiceSkillsManifestError(
                "Center Engine layout evidence has an invalid pool_center"
            )
        prefix = PurePosixPath(store_prefix)
        if (
            not store_prefix
            or prefix.is_absolute()
            or store_prefix != prefix.as_posix()
            or any(part in {"", ".", ".."} for part in prefix.parts)
        ):
            raise ServiceSkillsManifestError("invalid Center Store prefix")
        return cls(
            corpus="center",
            runtime_path=runtime_path,
            store_prefix=store_prefix,
            layout_contract_version=SERVICE_SKILLS_POOL_CONTRACT_VERSION,
        )

    def to_manifest(self) -> dict[str, str]:
        return {
            "corpus": self.corpus,
            "runtime_path": self.runtime_path,
            "store_prefix": self.store_prefix,
            "layout_contract_version": self.layout_contract_version,
            "permission": self.permission,
            "snapshot_policy": self.snapshot_policy,
        }


class ServiceSkillsManifestBuilder:
    """Build the Skills manifest embedded in one versioned service artifact."""

    def __init__(
        self,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        capability_reader: BotCapabilityStateReaderProtocol,
        center_store_prefix: str,
        center_store: CanonicalCenterVersionStore,
    ) -> None:
        self._layout_repository = layout_repository
        self._capability_reader = capability_reader
        self._center_store_prefix = center_store_prefix
        self._center_store = center_store

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

        try:
            assets = self._capability_reader.active_skill_assets(
                bot_id=str(bot.get("bot_id") or ""),
                owner_id=str(bot.get("owner_id") or bot.get("entity_id") or ""),
                bot=bot,
            )
            center_skills = tuple(
                sorted(
                    (
                        self._center_skill(asset)
                        for asset in assets
                        if asset.git_path.startswith("center://")
                    ),
                    key=lambda item: (
                        item["runtime_name"],
                        item["skill_uuid"],
                        item["sc_version_number"],
                    ),
                )
            )
        except Exception as exc:
            raise ServiceSkillsManifestError(
                "service build cannot freeze exact Center Skills"
            ) from exc

        shared_corpora: tuple[ResolvedSharedCorpusDelivery, ...] = ()
        if center_skills:
            if state.active_layout is not SkillLayout.POOL:
                raise ServiceSkillsManifestError(
                    "Center service build requires the Pool runtime layout"
                )
            shared_corpora = (
                ResolvedSharedCorpusDelivery.center_from_state(
                    state=state,
                    bot=bot,
                    store_prefix=self._center_store_prefix,
                ),
            )
            try:
                for item in center_skills:
                    ready = self._center_store.verify_version(
                        CanonicalCenterVersionRef(
                            CanonicalCenterVersionIdentity(
                                skill_uuid=item["skill_uuid"],
                                sc_version_number=item["sc_version_number"],
                            )
                        )
                    )
                    if not ready:
                        raise ServiceSkillsManifestError(
                            "Center service build requires every exact Store Version"
                        )
            except ServiceSkillsManifestError:
                raise
            except Exception as exc:
                raise ServiceSkillsManifestError(
                    "Center service build cannot verify every exact Store Version"
                ) from exc

        return CapturedServiceSkillsLayout(
            engine=engine,
            runtime_engine=runtime_layout_engine_for_bot(bot),
            scope=scope,
            active_layout=state.active_layout,
            phase=state.phase,
            migration_generation=state.migration_generation,
            # Capture the raw persisted value even for Legacy.  Legacy emits no
            # Pool contract, but a concurrent writer changing this field still
            # means the layout state moved while the physical snapshot ran.
            layout_contract_version=state.layout_contract_version,
            center_skills=center_skills,
            shared_corpora=shared_corpora,
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
        if captured.shared_corpora:
            current_delivery = ResolvedSharedCorpusDelivery.center_from_state(
                state=current,
                bot={
                    "active_engine": captured.runtime_engine,
                },
                store_prefix=self._center_store_prefix,
            )
            if (current_delivery,) != captured.shared_corpora:
                raise ServiceSkillsManifestError(
                    "Engine shared corpus delivery changed during service build"
                )

        manifest = {
            "schema_version": 1,
            "engine": engine,
            "active_layout": captured.active_layout.value,
            "layout_contract_version": (
                captured.layout_contract_version
                if captured.active_layout is SkillLayout.POOL
                else None
            ),
        }
        if captured.center_skills:
            manifest["center_skills"] = [
                dict(item) for item in captured.center_skills
            ]
            manifest["shared_corpora"] = [
                delivery.to_manifest() for delivery in captured.shared_corpora
            ]
        return manifest

    @staticmethod
    def _center_skill(asset) -> dict[str, Any]:
        RuntimeNamePolicy.name_for(asset)
        identity = CanonicalCenterVersionIdentity(
            skill_uuid=asset.skill_uuid,
            sc_version_number=asset.sc_version_number,
        )
        dependencies = list(asset.mcp_dependencies)
        mcp_dependency_codes(dependencies)
        return {
            "runtime_name": asset.name,
            "skill_uuid": identity.skill_uuid,
            "sc_version_number": identity.sc_version_number,
            "mcp_dependencies": dependencies,
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
    center_skills = manifest.get("center_skills")
    if center_skills is not None:
        if not isinstance(center_skills, list):
            raise ServiceSkillsManifestError("center_skills must be an array")
        normalized: list[tuple[str, str, str]] = []
        for item in center_skills:
            if not isinstance(item, dict) or set(item) != {
                "runtime_name",
                "skill_uuid",
                "sc_version_number",
                "mcp_dependencies",
            }:
                raise ServiceSkillsManifestError(
                    "invalid exact Center Skill manifest entry"
                )
            runtime_name = item["runtime_name"]
            if (
                not isinstance(runtime_name, str)
                or not runtime_name
                or runtime_name.strip() != runtime_name
                or "/" in runtime_name
                or "\\" in runtime_name
            ):
                raise ServiceSkillsManifestError("invalid Center runtime name")
            try:
                identity = CanonicalCenterVersionIdentity(
                    skill_uuid=item["skill_uuid"],
                    sc_version_number=item["sc_version_number"],
                )
                dependencies = item["mcp_dependencies"]
                if not isinstance(dependencies, list):
                    raise ValueError
                mcp_dependency_codes(dependencies)
            except Exception as exc:
                raise ServiceSkillsManifestError(
                    "invalid exact Center Skill manifest entry"
                ) from exc
            normalized.append(
                (
                    runtime_name,
                    identity.skill_uuid,
                    identity.sc_version_number,
                )
            )
        if normalized != sorted(normalized) or len(normalized) != len(set(normalized)):
            raise ServiceSkillsManifestError(
                "Center Skill manifest must be unique and stably sorted"
            )

    shared_corpora = manifest.get("shared_corpora")
    if center_skills:
        if not isinstance(shared_corpora, list) or len(shared_corpora) != 1:
            raise ServiceSkillsManifestError(
                "Center Skill manifest requires one frozen shared corpus"
            )
        _parse_frozen_center_delivery(shared_corpora[0])
    elif shared_corpora is not None:
        raise ServiceSkillsManifestError(
            "shared Center corpus requires exact Center Skills"
        )


def _parse_frozen_center_delivery(value: object) -> ResolvedSharedCorpusDelivery:
    if not isinstance(value, dict) or set(value) != {
        "corpus",
        "runtime_path",
        "store_prefix",
        "layout_contract_version",
        "permission",
        "snapshot_policy",
    }:
        raise ServiceSkillsManifestError("invalid shared Center corpus delivery")
    try:
        delivery = ResolvedSharedCorpusDelivery(**value)
    except TypeError as exc:
        raise ServiceSkillsManifestError(
            "invalid shared Center corpus delivery"
        ) from exc
    if (
        delivery.corpus != "center"
        or delivery.layout_contract_version != SERVICE_SKILLS_POOL_CONTRACT_VERSION
        or delivery.permission != "read_only"
        or delivery.snapshot_policy != "exclude"
    ):
        raise ServiceSkillsManifestError("invalid shared Center corpus delivery")
    path = PurePosixPath(delivery.runtime_path)
    prefix = PurePosixPath(delivery.store_prefix)
    if (
        not path.is_absolute()
        or delivery.runtime_path != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or not delivery.store_prefix
        or prefix.is_absolute()
        or delivery.store_prefix != prefix.as_posix()
        or any(part in {"", ".", ".."} for part in prefix.parts)
    ):
        raise ServiceSkillsManifestError("invalid shared Center corpus delivery")
    return delivery


def frozen_center_delivery_from_ext(
    ext: dict[str, Any] | None,
    bot: dict[str, Any],
) -> ResolvedSharedCorpusDelivery | None:
    """Read only the exact delivery frozen in this historical artifact."""

    manifest = (ext or {}).get("skills_manifest")
    if manifest is None:
        return None
    if not isinstance(manifest, dict):
        raise ServiceSkillsManifestError("invalid service Skills manifest")
    validate_service_skills_manifest_for_release(manifest, bot)
    shared = manifest.get("shared_corpora")
    if shared is None:
        return None
    return _parse_frozen_center_delivery(shared[0])


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
    "ResolvedSharedCorpusDelivery",
    "frozen_center_delivery_from_ext",
    "service_skills_manifest_env",
    "service_skills_env_from_ext",
    "validate_service_skills_manifest_for_release",
]
