"""Store-backed write targets for the teclaw strategy (W8).

The identity and resources materialisers write through two narrow ports
(``apply/identity_port.py``, ``apply/resource_port.py``) that the ARCA
strategy binds to the real device-backed services. The teclaw strategy, with
the platform-managed switch on, binds them to these: the same method
signatures, but every read and write goes to the managed-files store — the
platform's own copy — and never to a container. The materialisers do not know
which they were handed (spec D-7).

**Convergence is observed from the store.** ``list_bot_files`` /
``read_identity_file`` / ``exists`` answer from what the store holds, so an
unchanged file plans ``unchanged`` and writes nothing, exactly as the
device-backed ports make the materialisers behave.

**Paths.** Identity files live under the ``identity`` namespace by their
file type (``identity/RULES.md``); resources under ``workspace`` by their
declared path (``workspace/kb/faq.md``); a local skill package's files under
``workspace/skills-local/<name>/`` (``PlatformSkillPackageUpload``, the ``skills``
materialiser's upload road). A resource "directory" is the set of files under
its prefix — there is no directory object — so a tree delete is a prefix
delete over the store's listing.

Every method is ``async`` because the port protocols are; the store itself is
synchronous (an object-store client), so the work runs in a thread, the way
the promotion step drives its object writes.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Mapping, Optional

from agentclaw.community.core.bot_config_manifest.managed_files.store import (
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    CATEGORY_SKILLS,
    IDENTITY_NS,
    OWNER_ENTITY_TYPE,
    SKILLS_LOCAL_DIR,
    WORKSPACE_NS,
    ManagedFile,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.ports.skill_package_upload_port import (
    SkillPackageUploadPort,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPackageValidator,
)


class _StorePort:
    """Every method below has the signature of the port protocol it fills
    (``ManifestIdentityPort``, ``ManifestResourcePort``, the skills upload
    road) — the device-backed services' own signatures, which the
    materialisers call by keyword. The parameters a store-backed port does
    not need (``owner_id``, ``engine_type``, ``stage``, ``operator_id``) are
    therefore accepted and left unused rather than dropped: dropping one
    would break the call, and the shape is the seam (spec D-7)."""

    def __init__(self, store: ManagedFilesStore) -> None:
        self._store = store

    @staticmethod
    def _scope(entity_type: str, entity_id: str, bot_id: str) -> ManagedFileScope:
        return ManagedFileScope(entity_type=entity_type, entity_id=entity_id, bot_id=bot_id)


class StoreIdentityPort(_StorePort):
    """``ManifestIdentityPort`` over the store: one object per identity file."""

    async def list_bot_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> list[tuple[str, bool]]:
        scope = self._scope(entity_type, entity_id, bot_id)
        rows = await asyncio.to_thread(self._store.list, scope, category=CATEGORY_IDENTITY)
        return [(row.name, True) for row in rows]

    async def read_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> str:
        scope = self._scope(entity_type, entity_id, bot_id)
        content = await asyncio.to_thread(
            self._store.read_at, scope, _identity_path(file_type)
        )
        return content.decode("utf-8") if content is not None else ""

    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
        *,
        stage: str = "draft",
    ) -> Any:
        scope = self._scope(entity_type, entity_id, bot_id)
        rel_path = _identity_path(file_type)
        if not content:
            # The domain's own "absent": an empty write removes the file. In
            # the store that is a delete, so the composer never references an
            # empty object.
            await asyncio.to_thread(
                self._store.delete, scope, rel_path=rel_path
            )
            return {"file_type": file_type, "removed": True}
        file = await asyncio.to_thread(
            self._store.put,
            scope,
            category=CATEGORY_IDENTITY,
            name=file_type,
            rel_path=rel_path,
            content=content.encode("utf-8"),
        )
        return {"file_type": file_type, "digest": file.digest}


class StoreResourcePort(_StorePort):
    """``ManifestResourcePort`` over the store: objects under ``workspace/``."""

    async def upload_file(
        self,
        *,
        entity_type: str = OWNER_ENTITY_TYPE,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
    ) -> dict[str, Any]:
        scope = self._scope(entity_type, entity_id, bot_id)
        declared = f"{target_dir.strip('/')}/{filename}" if target_dir.strip("/") else filename
        file = await asyncio.to_thread(
            self._store.put,
            scope,
            category=CATEGORY_RESOURCES,
            name=declared,
            rel_path=_workspace_path(declared),
            content=data,
        )
        return {"path": declared, "digest": file.digest, "size_bytes": file.size_bytes}

    async def delete(
        self,
        *,
        entity_type: str = OWNER_ENTITY_TYPE,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        """Delete a file, or every file under a directory. ``False`` when
        nothing was there — the write chain's own contract, which the
        materialiser disambiguates by re-probing ``exists``."""
        scope = self._scope(entity_type, entity_id, bot_id)
        target = path.strip("/")
        rel = _workspace_path(target)
        removed = False
        rows: list[ManagedFile] = await asyncio.to_thread(
            self._store.list, scope, category=CATEGORY_RESOURCES
        )
        for row in rows:
            if row.rel_path == rel or row.rel_path.startswith(rel + "/"):
                # Listed, so it was there; the delete raises if it did not land.
                await asyncio.to_thread(
                    self._store.delete, scope, rel_path=row.rel_path
                )
                removed = True
        return removed

    async def exists(
        self,
        *,
        entity_type: str = OWNER_ENTITY_TYPE,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool:
        scope = self._scope(entity_type, entity_id, bot_id)
        rel = _workspace_path(path.strip("/"))
        rows: list[ManagedFile] = await asyncio.to_thread(
            self._store.list, scope, category=CATEGORY_RESOURCES
        )
        return any(row.rel_path == rel or row.rel_path.startswith(rel + "/") for row in rows)


class PlatformSkillPackageUpload(_StorePort, SkillPackageUploadPort):
    """The ``skills`` materialiser's upload road, over the store.

    The real road (``LocalSkillUploadService``) validates the zip, writes the
    package to the bot's ``skills-local`` directory on the device and records
    a skill row whose locator is ``local://skills-local/<name>`` — the minimal
    logical path the teclaw layout already stores. This port keeps every
    observable but the device: the same validator, one object per package
    file under ``workspace/skills-local/<name>/…`` (category ``skills``,
    named by the skill), the same skill row with the same locator, and the
    same return shape. Activation is not this port's: the materialiser calls
    the activation service with the id this returns.

    ``installed_package_digest`` answers the real service's question the
    real service's way — the sha256 of the canonical repack of the files
    actually stored under the name — so an unchanged package plans
    ``unchanged`` on the second apply and writes nothing.
    """

    def __init__(
        self,
        store: ManagedFilesStore,
        *,
        validator: SkillPackageValidator,
        skill_repository: SkillRepository,
    ) -> None:
        super().__init__(store)
        self._validator = validator
        self._skills = skill_repository

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        # The same gate the real service runs: the package's own SKILL.md
        # names the skill, and an invalid zip is refused here, not stored.
        validated = self._validator.validate_zip(package)
        return await asyncio.to_thread(
            self._install, bot_id, owner_id, actor_id, validated.name, validated
        )

    def _install(self, bot_id: str, owner_id: str, actor_id: str, name: str, validated) -> dict:
        scope = self._scope(OWNER_ENTITY_TYPE, owner_id, bot_id)
        prefix = _skill_prefix(name)
        wanted: set[str] = set()
        for relative, content in validated.files:
            rel_path = f"{prefix}/{relative}"
            wanted.add(rel_path)
            self._store.put(
                scope,
                category=CATEGORY_SKILLS,
                name=name,
                rel_path=rel_path,
                content=content,
            )
        # New files first, stale ones after: the package is never missing a
        # member between the two, and a replaced file was overwritten in place.
        for row in self._store.list(scope, category=CATEGORY_SKILLS):
            if _under(row.rel_path, prefix) and row.rel_path not in wanted:
                self._store.delete(scope, rel_path=row.rel_path)

        locator = f"local://{SKILLS_LOCAL_DIR}/{name}"
        existing = _own_row(
            self._skills.list_bot_local_by_name(bot_id=bot_id, name=name), owner_id
        )
        if existing is None:
            skill = self._skills.create(
                {
                    "name": name,
                    "description": validated.description,
                    "git_path": locator,
                    "category": "general",
                    "tags": "[]",
                    "is_public": False,
                    "user_id": owner_id,
                    "bolt_id": bot_id,
                    "source_type": "upload",
                }
            )
            operation = "created"
        else:
            skill = self._skills.update(
                str(existing["id"]),
                {"description": validated.description, "git_path": locator},
            ) or existing
            operation = "replaced"
        return {
            "operation": operation,
            "skill": {**skill, "active": False},
            "actor_id": actor_id,
        }

    async def installed_package_digest(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str, name: str
    ) -> Optional[str]:
        return await asyncio.to_thread(self._digest, bot_id, owner_id, name)

    def _digest(self, bot_id: str, owner_id: str, name: str) -> Optional[str]:
        scope = self._scope(OWNER_ENTITY_TYPE, owner_id, bot_id)
        prefix = _skill_prefix(name)
        files: list[tuple[str, bytes]] = []
        for row in self._store.list(scope, category=CATEGORY_SKILLS):
            if not _under(row.rel_path, prefix):
                continue
            content = self._store.read(row)
            if content is None:
                # A member that vanished between the listing and the read:
                # unknown, never equal — the write path restores the package
                # while it is in hand.
                return None
            files.append((row.rel_path[len(prefix) + 1 :], content))
        if not files:
            return None
        try:
            canonical = self._validator.pack_directory(files)
        except (SkillPackageInvalidError, SkillPackageTooLargeError):
            return None
        return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _skill_prefix(name: str) -> str:
    return f"{WORKSPACE_NS}/{SKILLS_LOCAL_DIR}/{name}"


def _under(rel_path: str, prefix: str) -> bool:
    return rel_path.startswith(prefix + "/")


class ManagedSkillOwnerConflict(RuntimeError):
    """A same-name local skill row exists that the uploader does not own.

    The real upload road never replaces a foreign-owned or legacy unowned row
    (``LocalSkillUploadService._same_name_matches``); neither does this port.
    Raised from the materialiser's write, so the entry reports ``failed``
    rather than another collaborator's row being overwritten.
    """


def _own_row(rows: list[dict[str, Any]], owner_id: str) -> Optional[dict[str, Any]]:
    """The uploader's own same-name row, or ``None`` when there is none.

    A row someone else owns — or a legacy row with no owner — is never a
    replacement target: with such rows present and none of the uploader's,
    the upload is refused.
    """
    for row in rows:
        if str(row.get("user_id") or "") == owner_id:
            return row
    if rows:
        raise ManagedSkillOwnerConflict(
            f"a local skill named {rows[0].get('name')!r} exists on this bot under "
            "another owner; it cannot be replaced by this upload"
        )
    return None


def _identity_path(file_type: str) -> str:
    return f"{IDENTITY_NS}/{file_type}"


def _workspace_path(declared: str) -> str:
    return f"{WORKSPACE_NS}/{declared.lstrip('/')}"


__all__ = [
    "ManagedSkillOwnerConflict",
    "StoreIdentityPort",
    "StoreResourcePort",
    "PlatformSkillPackageUpload",
]
