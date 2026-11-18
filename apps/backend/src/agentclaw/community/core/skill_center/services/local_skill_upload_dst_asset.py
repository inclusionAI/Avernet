"""Dst-local-skill asset registration for bot-file-synced Local Skills.

Split out of :class:`LocalSkillUploadService` so the service module stays
under the architecture module-size cap. This mixin holds ``one`` method —
``ensure_dst_local_skill_asset`` — the method the ``from: from_bot`` feature
added: it registers the dst ``ac_skill`` row for a member whose package
arrives via BOT_FILE_SYNC (no upload, no package read, no
``LocalSkillPackageRuntime``). Every pre-existing upload-road persistence
method (``_create`` / ``_replace`` / ``_commit_runtime_*`` / ``_discard`` / …)
stays on the service itself — unchanged, unmoved.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.addressing import (
    stable_resource_owner_id,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class LocalSkillUploadDstAssetMixin:
    """Register the dst ``ac_skill`` row for a bot-file-synced Local Skill.

    Mixed in **ahead of** :class:`LocalSkillUploadServiceProtocol` on the
    service class: that protocol carries a no-body stub for
    :meth:`ensure_dst_local_skill_asset`, and a Protocol listed first would
    shadow this concrete implementation. First base wins, so the mixin must
    precede the protocol in the MRO.
    """

    async def ensure_dst_local_skill_asset(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        source_bot_id: str, source_owner_id: str, name: str,
    ) -> dict[str, Any]:
        """Register the dst ``ac_skill`` row for a bot-file-synced Local Skill.

        A ``from: from_bot`` member reaches this bot through BOT_FILE_SYNC:
        the sync writes the source bot's workspace onto this one, carrying the
        package's bytes to the dst ``skills-local`` directory that the upload
        road would write to. This method does not call
        :class:`LocalSkillPackageRuntime` — file-sync already converged the
        package — and does not read, validate or upload the bytes. It
        reverse-looks-up the source bot's ``ac_skill`` row for
        ``name``/``description``, recomputes the dst ``git_path`` through the
        same :class:`SkillServiceFactory` call the upload road takes, and
        writes the dst ``ac_skill`` row (create-or-replace same-name owned
        row), under the same edit guard the upload road uses so a concurrent
        manual upload cannot interleave.

        ``digest`` is intentionally not propagated: a runtime-time digest probe
        belongs to the file-sync layer that owns the package, not the row
        registration here.
        """
        initial_bot = self._authorize(bot_id, owner_id, actor_id)
        scope = self._scope_for(initial_bot, bot_id)
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditBusyError as exc:
            raise LocalSkillEditBusyError() from exc
        except SkillsPoolEditRollbackError as exc:
            raise LocalSkillLayoutRollbackError() from exc
        except SkillsPoolEditLockUnavailableError as exc:
            raise LocalSkillEditLockUnavailableError() from exc
        except SkillsPoolEditPausedError as exc:
            raise LocalSkillEditPausedError() from exc
        try:
            bot = self._authorize(bot_id, owner_id, actor_id)
            if self._scope_for(bot, bot_id) != scope:
                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            stable_owner = stable_resource_owner_id(bot)
            # Re-read same-name candidates under the lock — the same race
            # the upload road guards against. ``_same_name_matches`` raises
            # ``LocalSkillOwnerAmbiguousError`` for unowned legacy rows; that
            # surfaces here untouched, the same as the upload road.
            matches = self._same_name_matches(
                bot_id=bot_id, owner_id=stable_owner, name=name
            )
            if len(matches) > 1:
                raise LocalSkillDuplicateError()
            # Reverse-lookup the source row. ``list_bot_local_by_name``
            # returns metadata-only rows for every same-name local asset on
            # the source bot; filter to the source owner — a ``bot_id`` is
            # not globally unique, and a shared or template id can collide.
            src_rows = self._skill_repo.list_bot_local_by_name(
                bot_id=source_bot_id, name=name
            )
            src_owned = [
                row for row in src_rows
                if str(row.get("user_id") or "") == source_owner_id
            ]
            if len(src_owned) != 1:
                # Zero or several: the manifest's source declaration and the
                # source bot's actual state disagreed. Surface it as
                # ``LocalSkillNotFoundError``, the same currency the upload
                # road uses for "no source row to install".
                raise LocalSkillNotFoundError(
                    f"source bot {source_bot_id!r} has no unique local skill "
                    f"named {name!r} owned by {source_owner_id!r}"
                )
            src = src_owned[0]
            description = src.get("description") or ""
            is_teclaw = self._is_teclaw(bot_id=bot_id, owner_id=stable_owner)
            location = self._skill_service_factory.local_skill_package_location(
                entity_id=str(bot["entity_id"]),
                owner_id=stable_owner,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=is_teclaw,
                name=name,
            )
            dst_git_path = f"local://{location.directory}"
            if matches:
                # Preserve the existing locator the way ``_commit_runtime_replace``
                # does: a replace never moves git_path, only refreshes the
                # description on the row that file-sync already owns.
                existing = matches[0]
                old_locator = str(existing["git_path"])[len("local://"):]
                replaced = self._skill_repo.replace_bot_local_skill(
                    skill_id=str(existing["id"]),
                    owner_id=stable_owner,
                    bot_id=bot_id,
                    old_locator=old_locator,
                    new_locator=old_locator,
                    description=description,
                )
                if replaced is None:
                    raise LocalSkillStorageError()
                return {
                    "operation": "updated",
                    "skill": {
                        **existing,
                        "description": description,
                        "git_path": existing["git_path"],
                        "user_id": stable_owner,
                    },
                    "actor_id": actor_id,
                }
            try:
                skill = self._skill_repo.create(
                    {
                        "name": name,
                        "description": description,
                        "git_path": dst_git_path,
                        "category": "general",
                        "tags": "[]",
                        "is_public": False,
                        "user_id": stable_owner,
                        "bolt_id": bot_id,
                        "source_type": "upload",
                    }
                )
            except Exception as exc:
                logger.error(
                    "[LocalSkillUploadService] ensure_dst_local_skill_asset "
                    "persistence failed bot_id=%s skill_name=%s "
                    "source_bot_id=%s target_path=%s",
                    bot_id, name, source_bot_id, dst_git_path,
                )
                raise LocalSkillStorageError() from exc
            return {
                "operation": "created",
                "skill": {**skill, "active": False},
                "actor_id": actor_id,
            }
        finally:
            self._edit_guard.release(lease)
