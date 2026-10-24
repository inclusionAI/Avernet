"""Default provenance reads shared by Manifest planning and transactional writes."""

from __future__ import annotations

from contextlib import contextmanager

import hashlib
import json

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.skill_installation_source import (
    SkillInstallationSourceKind,
)
from agentclaw.community.core.repository.implementations.skill_center.bot_skillset_installations import (
    set_member_skill_ids,
    set_member_mcp_codes,
)
from agentclaw.community.core.repository.implementations.skill_center.tables import (
    default_exclusions,
    skill_installations,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_mcp_dependencies import (
    skill_projection_mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.manifest_defaults import (
    ManifestDefaults,
    PackageWriteGuard,
    ManifestDefaultsConflictError,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class ManifestDefaultQueries:
    """Mixed into the desired-state UoW, with the same scope and Set resolver."""

    def manifest_skill_package_guard(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        expected_defaults: ManifestDefaults,
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> PackageWriteGuard:
        @contextmanager
        def guard():
            # Resolve ids without retaining a range lock on every tenant/Bot
            # scanned by a legacy schema. The upload road already serializes
            # same-Bot creation; existing assets serialize with Default writers
            # on their primary-key row locks below.
            with self._db.orm_session() as lookup:
                target_locators = dict(self._scope(lookup.query(Skill.id, Skill.git_path), Skill).filter(
                    Skill.bolt_id == bot_id, Skill.user_id == owner_id,
                    Skill.name == name, Skill.git_path.like("local://%"),
                ).all())
                target_ids = sorted(target_locators)
            with self._db.transactional_orm_session() as session:
                targets = session.query(Skill).filter(Skill.id.in_(target_ids)).order_by(Skill.id).with_for_update().all() if target_ids else []
                if len(targets) != len(target_ids) or any(
                    row.bolt_id != bot_id or row.user_id != owner_id or row.name != name
                    or row.git_path != target_locators[row.id] or not row.git_path.startswith("local://")
                    or row.env != get_current_env() or row.avernet_tenant != get_current_avernet_tenant()
                    for row in targets
                ):
                    raise ManifestDefaultsConflictError("Local package identity changed before publication")
                sets = self._bot_sets(
                    session,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    engine_type=engine_type,
                    default_engine_types=default_engine_types,
                    locked=False,
                )
                protected = self._check_manifest_defaults(
                    session,
                    expected=expected_defaults,
                    sets=sets,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    platform_default_codes=expected_defaults.platform_codes,
                    locked=False,
                )
                if name in protected.skill_names:
                    raise ManifestDefaultsConflictError(
                        "Package conflicts with effective Default"
                    )
                # Membership/publication writers acquire the same Skill row lock.
                # Keep it only during the physical package write, not metadata/project.
                yield

        return guard

    def manifest_defaults(
        self,
        *,
        bot_id: str,
        owner_id: str,
        check_skill_sources: bool = True,
        platform_default_codes: frozenset[str],
        engine_type: str | None = None,
        default_engine_types: tuple[str, ...] | None = None,
    ) -> ManifestDefaults:
        with self._db.transactional_orm_session() as session:
            default_exclusions.lock_bot_exclusions(
                session, bot_id=bot_id, owner_id=owner_id
            )
            sets = self._bot_sets(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                engine_type=engine_type,
                default_engine_types=default_engine_types,
                locked=True,
            )
            return self._manifest_defaults_in_session(
                session,
                sets=sets,
                bot_id=bot_id,
                owner_id=owner_id,
                platform_default_codes=platform_default_codes,
                check_skill_sources=check_skill_sources,
            )

    def _manifest_defaults_in_session(
        self,
        session,
        *,
        sets,
        bot_id: str,
        owner_id: str,
        platform_default_codes: frozenset[str],
        check_skill_sources: bool,
        locked: bool = True,
    ) -> ManifestDefaults:
        ids: set[int] = set()
        codes: set[str] = set(platform_default_codes)
        facts: list = []
        defaults = [s for s in sets if s.is_default]
        for row in defaults:
            sid = int(row.id)
            members = set_member_skill_ids(
                self._scope, session, skill_set_id=sid, locked=locked
            )
            mcps = set_member_mcp_codes(
                self._scope, session, skill_set_id=sid, locked=locked
            )
            excluded_skills = default_exclusions.excluded_skill_ids(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=sid,
                locked=locked,
            )
            excluded_mcps = default_exclusions.excluded_mcp_codes(
                session,
                bot_id=bot_id,
                owner_id=owner_id,
                set_id=sid,
                locked=locked,
            )
            ids.update(members - excluded_skills)
            codes.update((mcps | set(platform_default_codes)) - excluded_mcps)
            facts.append(
                (
                    sid,
                    sorted(members),
                    sorted(mcps),
                    sorted(excluded_skills),
                    sorted(excluded_mcps),
                )
            )
        all_mcp_exclusions = default_exclusions.all_mcp_exclusions(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
        )
        codes.difference_update(code for _, code in all_mcp_exclusions)
        default_members = {int(fact[0]): set(fact[1]) - set(fact[3]) for fact in facts}
        excluded_codes = {code for _, code in all_mcp_exclusions}
        facts = [
            (
                sid,
                sorted(members),
                sorted((set(mcps) | set(platform_default_codes)) - excluded_codes),
            )
            for sid, _, mcps, _, _ in facts
            for members in [default_members[sid]]
        ]

        sources = skill_installations.sources(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            env=get_current_env(),
            locked=locked,
        )
        installed = skill_installations.installed_ids(
            session,
            bot_id=bot_id,
            owner_id=owner_id,
            env=get_current_env(),
            locked=locked,
        )
        if check_skill_sources and installed - sources.keys():
            raise ManifestDefaultsConflictError(
                "Skill provenance is unknown; no automatic repair"
            )
        if check_skill_sources and any(
            kind not in {k.value for k in SkillInstallationSourceKind}
            for kind, _ in sources.values()
        ):
            raise ManifestDefaultsConflictError("Skill provenance is inconsistent")
        if check_skill_sources:
            ordinary_sets = {int(row.id): row for row in sets if not row.is_default}
            for skill_id, (kind, source_set_id) in sources.items():
                direct = kind == SkillInstallationSourceKind.DIRECT.value
                default = kind == SkillInstallationSourceKind.DEFAULT.value
                if direct != (source_set_id is None):
                    raise ManifestDefaultsConflictError(
                        "Skill provenance has an invalid source Set"
                    )
                if default != (skill_id in ids) or (
                    default
                    and skill_id not in default_members.get(source_set_id, set())
                ):
                    raise ManifestDefaultsConflictError(
                        "Skill provenance contradicts effective Default supply"
                    )
                if kind == SkillInstallationSourceKind.SKILL_SET.value:
                    if (
                        source_set_id not in ordinary_sets
                        or skill_id
                        not in set_member_skill_ids(
                            self._scope,
                            session,
                            skill_set_id=source_set_id,
                            locked=locked,
                        )
                    ):
                        raise ManifestDefaultsConflictError(
                            "Skill provenance references an unknown Set membership"
                        )
        asset_query = self._scope(session.query(Skill), Skill).filter(Skill.id.in_(ids))
        assets = (asset_query.with_for_update() if locked else asset_query).all() if ids else []
        if len(assets) != len(ids) or len({s.name for s in assets}) != len(ids):
            raise ManifestDefaultsConflictError(
                "Default Skill identities cannot be resolved uniquely"
            )
        for asset in sorted(assets, key=lambda s: s.id):
            facts.append(
                (
                    str(asset.id),
                    asset.name,
                    sorted(skill_projection_mcp_dependency_codes(session, asset, locked=locked)),
                )
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                [facts, sorted(platform_default_codes)],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return ManifestDefaults(
            frozenset(str(i) for i in ids),
            frozenset(a.name for a in assets),
            frozenset(codes),
            platform_default_codes,
            fingerprint,
            check_skill_sources,
        )

    @staticmethod
    def _manifest_platform_codes(expected: ManifestDefaults) -> frozenset[str]:
        return expected.platform_codes

    def _check_manifest_defaults(
        self,
        session,
        *,
        expected: ManifestDefaults,
        sets,
        bot_id: str,
        owner_id: str,
        platform_default_codes: frozenset[str],
        locked: bool = True,
    ) -> ManifestDefaults:
        actual = self._manifest_defaults_in_session(
            session,
            sets=sets,
            bot_id=bot_id,
            owner_id=owner_id,
            platform_default_codes=platform_default_codes,
            check_skill_sources=expected.check_skill_sources,
            locked=locked,
        )
        if actual != expected:
            raise ManifestDefaultsConflictError(
                "Default supply changed; re-plan this category"
            )
        return actual
