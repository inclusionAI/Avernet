"""Store-backed write targets for the teclaw strategy (W8).

The identity and resources materialisers write through two narrow ports
(``apply/identity_port.py``, ``apply/resource_port.py``) that the ARCA
strategy binds to the real device-backed services. The teclaw strategy, with
the platform-managed switch on, binds them to these: the same method
signatures, but every read and write goes to the managed-files store — the
platform's own copy — and never to a container. The materialisers do not know
which they were handed (spec D-7).

**Convergence is observed from the index.** ``list_bot_files`` /
``read_identity_file`` / ``exists`` answer from rows, so an unchanged digest
plans ``unchanged`` and writes nothing, exactly as the device-backed ports
make the materialisers behave.

**Paths.** Identity files live under the ``identity`` namespace by their
file type (``identity/RULES.md``); resources under ``workspace`` by their
declared path (``workspace/kb/faq.md``). A resource "directory" is the set of
rows under its prefix — there is no directory object — so a tree delete is a
prefix delete over the index.

Every method is ``async`` because the port protocols are; the store itself is
synchronous (an object-store client plus a repository), so the work runs in a
thread, the way the promotion step drives its object writes.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from agentclaw.community.core.bot_config_manifest.managed_files.store import (
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    IDENTITY_NS,
    WORKSPACE_NS,
    ManagedFile,
    ManagedFileScope,
    ManagedFilesStore,
)


class _StorePort:
    def __init__(
        self,
        store: ManagedFilesStore,
        *,
        env: Callable[[], str],
        apply_id: Callable[[], Optional[str]] = lambda: None,
    ) -> None:
        self._store = store
        self._env = env
        # Which apply is writing — a thunk so the port, built once per apply
        # by the strategy, can be handed the id after the fact.
        self._apply_id = apply_id

    def _scope(self, entity_type: str, entity_id: str, bot_id: str) -> ManagedFileScope:
        return ManagedFileScope(
            env=self._env(), entity_type=entity_type, entity_id=entity_id, bot_id=bot_id
        )


class StoreIdentityPort(_StorePort):
    """``ManifestIdentityPort`` over the store: one row per identity file."""

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
        row = await asyncio.to_thread(
            self._store.get, scope, category=CATEGORY_IDENTITY, rel_path=_identity_path(file_type)
        )
        if row is None:
            return ""
        content = await asyncio.to_thread(self._store.read, row)
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
            # the store that is a delete, so the index never carries an empty
            # object the composer would then reference.
            removed = await asyncio.to_thread(
                self._store.delete, scope, category=CATEGORY_IDENTITY, rel_path=rel_path
            )
            return {"file_type": file_type, "removed": removed}
        file = await asyncio.to_thread(
            self._store.put,
            scope,
            category=CATEGORY_IDENTITY,
            name=file_type,
            rel_path=rel_path,
            content=content.encode("utf-8"),
            apply_id=self._apply_id(),
        )
        return {"file_type": file_type, "digest": file.digest}


class StoreResourcePort(_StorePort):
    """``ManifestResourcePort`` over the store: rows under ``workspace/``."""

    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
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
            apply_id=self._apply_id(),
        )
        return {"path": declared, "digest": file.digest, "size_bytes": file.size_bytes}

    async def delete(
        self,
        *,
        entity_type: str = "staff",
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
                if await asyncio.to_thread(
                    self._store.delete, scope, category=CATEGORY_RESOURCES, rel_path=row.rel_path
                ):
                    removed = True
        return removed

    async def exists(
        self,
        *,
        entity_type: str = "staff",
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


def _identity_path(file_type: str) -> str:
    return f"{IDENTITY_NS}/{file_type}"


def _workspace_path(declared: str) -> str:
    return f"{WORKSPACE_NS}/{declared.lstrip('/')}"


__all__ = ["StoreIdentityPort", "StoreResourcePort"]
