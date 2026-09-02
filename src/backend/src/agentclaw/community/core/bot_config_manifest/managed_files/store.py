"""The platform's own copy of a teclaw bot's manifest-delivered files (W8).

On teclaw the artifact is the delivery, so what the manifest applies has to
live somewhere the composer can read: bytes in the bot-data object store, one
index row per file (``ac_bot_config_managed_files``). This store is that pair
behind one door. The store-backed ports (``ports.py``) converge it; the reader
(``reader.py``) composes from it; nothing else writes it.

**Key layout.** The promotion step already stages a teclaw bot's files under
``{bot-data base}/{entity_type}_{entity_id}/{bot_id}_{publish_id}_{stage}/teclaw/{ns}/{rel}``
so the ``bot-data`` store's base resolves them. Manifest-delivered files take
the same shape with a ``_manifest`` segment where the publish stage would be::

    teclaw/{env}/bolt_data/staff_u1/bot7_manifest/teclaw/identity/RULES.md
    └────── store base ─────┘└──────── ref path (artifact) ───────────────┘

The artifact ref's ``path`` is everything after the base; the engine-relative
``rel_path`` the index keys on is ``{ns}/{rel}`` (``identity/RULES.md``,
``workspace/kb/faq.md``, ``workspace/skills-local/<name>/SKILL.md``).

**Object before row, both ways.** ``put`` writes the object and only then the
row, so a row without an object cannot exist. ``delete`` removes the object
first and only then the row: an object delete that fails raises with the row
still in place, so the index never forgets bytes it can no longer reach — a
later delete or purge finds the row and tries again. The moment between the
two, where a row names an object already gone, is the same moment ``put``
has the other way round, and the composer reads through the index at the
next compose, not during a write.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Optional

from agentclaw.community.core.bot_config_manifest.repository.managed_files_models import (
    ManagedFileRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManagedFilesRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

logger = get_logger()

#: The composer store id whose base is the teclaw bot-data prefix.
BOT_DATA_STORE = "bot-data"
#: The two engine namespaces (``core/config_compose/teclaw_paths.py``).
IDENTITY_NS = "identity"
WORKSPACE_NS = "workspace"
#: Where a local skill package lives under the workspace namespace.
SKILLS_LOCAL_DIR = "skills-local"
#: The scope's entity type. The identity and resources materialisers address
#: every bot at ``("staff", owner_id)`` — the personal-bot surface's fixed
#: pair — so the index is keyed the same way on both the write and the read
#: side. The key prefix therefore reads ``staff_{owner}``, not the bot
#: record's ``{entity_type}_{entity_id}`` the promotion step uses; the two
#: never share a key (``_manifest`` versus a publish stage).
OWNER_ENTITY_TYPE = "staff"
#: The index categories.
CATEGORY_IDENTITY = "identity"
CATEGORY_RESOURCES = "resources"
CATEGORY_SKILLS = "skills"


class ManagedFilesStoreError(RuntimeError):
    """An object write or delete the store could not perform."""


@dataclass(frozen=True)
class ManagedFileScope:
    """Which bot a store call is about."""

    env: str
    entity_type: str
    entity_id: str
    bot_id: str

    @property
    def rel_root(self) -> str:
        return f"{self.entity_type}_{self.entity_id}/{self.bot_id}_manifest/teclaw"


@dataclass(frozen=True)
class ManagedFile:
    """One delivered file as the composer will reference it."""

    category: str
    name: str
    rel_path: str
    ref_path: str
    store_key: str
    digest: str
    size_bytes: int


def digest_of(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class ManagedFilesStore:
    """Bytes in the object store, rows in the index, one door."""

    def __init__(
        self,
        *,
        object_storage: ObjectStoragePlugin,
        repository: BotConfigManagedFilesRepositoryProtocol,
        store_base: Callable[[], str],
    ) -> None:
        self._oss = object_storage
        self._repo = repository
        # A thunk, like the composer's store coordinates: the base depends on
        # the deployment env and is read when a key is built, not at DI time.
        self._store_base = store_base

    # ── keys ─────────────────────────────────────────────────────────────

    def ref_path(self, scope: ManagedFileScope, rel_path: str) -> str:
        return f"{scope.rel_root}/{rel_path.lstrip('/')}"

    def store_key(self, scope: ManagedFileScope, rel_path: str) -> str:
        return f"{self._store_base().rstrip('/')}/{self.ref_path(scope, rel_path)}"

    # ── writes ───────────────────────────────────────────────────────────

    def put(
        self,
        scope: ManagedFileScope,
        *,
        category: str,
        name: str,
        rel_path: str,
        content: bytes,
        apply_id: Optional[str],
    ) -> ManagedFile:
        rel_path = rel_path.lstrip("/")
        key = self.store_key(scope, rel_path)
        if not self._oss.put_object(key, content):
            raise ManagedFilesStoreError(f"object store put failed for {key!r}")
        record = self._repo.upsert(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            category=category,
            name=name,
            rel_path=rel_path,
            store_key=key,
            digest=digest_of(content),
            size_bytes=len(content),
            apply_id=apply_id,
        )
        return self._to_file(scope, record)

    def delete(self, scope: ManagedFileScope, *, category: str, rel_path: str) -> bool:
        rel_path = rel_path.lstrip("/")
        record = self._repo.get(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            category=category,
            rel_path=rel_path,
        )
        if record is None:
            return False
        if not self._oss.delete_object(record.store_key):
            # The row stays: a purge or the next apply can still find the
            # object through it. Raised, so the category's write reports the
            # failure rather than the index forgetting reachable bytes.
            raise ManagedFilesStoreError(
                f"object store delete failed for {record.store_key!r}"
            )
        self._repo.delete(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            category=category,
            rel_path=rel_path,
        )
        return True

    def purge_owner_bot(self, owner_id: str, bot_id: str) -> int:
        """``purge`` for the bot the materialisers address at ``("staff", owner)``
        under the current env — the creation job's cleanup entry point."""
        from agentclaw.community.utils.env_utils import get_current_env

        return self.purge(
            ManagedFileScope(
                env=get_current_env(),
                entity_type=OWNER_ENTITY_TYPE,
                entity_id=owner_id,
                bot_id=bot_id,
            )
        )

    def purge(self, scope: ManagedFileScope) -> int:
        """Remove every row and object for the bot. Returns rows removed."""
        records = self._repo.list_all(
            env=scope.env, entity_id=scope.entity_id, bot_id=scope.bot_id
        )
        failed = [r.store_key for r in records if not self._oss.delete_object(r.store_key)]
        if failed:
            # The rows for the objects still standing stay too, so the next
            # purge reaches them; the creation job retries a discard that
            # did not land.
            for key in failed:
                logger.warning("[managed_files] object delete failed during purge: %s", key)
            raise ManagedFilesStoreError(
                f"object store delete failed for {len(failed)} object(s) during purge"
            )
        return self._repo.purge_bot(
            env=scope.env, entity_id=scope.entity_id, bot_id=scope.bot_id
        )

    # ── reads ────────────────────────────────────────────────────────────

    def get(
        self, scope: ManagedFileScope, *, category: str, rel_path: str
    ) -> Optional[ManagedFile]:
        record = self._repo.get(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            category=category,
            rel_path=rel_path.lstrip("/"),
        )
        return self._to_file(scope, record) if record is not None else None

    def list(self, scope: ManagedFileScope, *, category: str) -> list[ManagedFile]:
        return [
            self._to_file(scope, record)
            for record in self._repo.list_by_category(
                env=scope.env,
                entity_id=scope.entity_id,
                bot_id=scope.bot_id,
                category=category,
            )
        ]

    def read(self, file: ManagedFile) -> Optional[bytes]:
        return self._oss.get_object(file.store_key)

    def _to_file(self, scope: ManagedFileScope, record: ManagedFileRecord) -> ManagedFile:
        # The ref path is recomputed from the scope and the row's rel_path,
        # never sliced off the stored key: the composer's ``bot-data`` base is
        # the *current* one, and a row written under an earlier base must
        # still resolve against it (the object is re-put on the next apply;
        # a stale key would otherwise double the prefix and 404 every file).
        return ManagedFile(
            category=record.category,
            name=record.name,
            rel_path=record.rel_path,
            ref_path=self.ref_path(scope, record.rel_path),
            store_key=record.store_key,
            digest=record.digest,
            size_bytes=record.size_bytes,
        )


__all__ = [
    "BOT_DATA_STORE",
    "CATEGORY_IDENTITY",
    "CATEGORY_RESOURCES",
    "CATEGORY_SKILLS",
    "IDENTITY_NS",
    "ManagedFile",
    "ManagedFileScope",
    "ManagedFilesStore",
    "ManagedFilesStoreError",
    "OWNER_ENTITY_TYPE",
    "SKILLS_LOCAL_DIR",
    "WORKSPACE_NS",
    "digest_of",
]
