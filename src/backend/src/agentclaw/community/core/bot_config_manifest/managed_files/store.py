"""The platform's own copy of a teclaw bot's manifest-delivered files (W8).

On teclaw the artifact is the delivery, so what the manifest applies has to
live somewhere the composer can read: the bot-data object store, under a key
layout that *is* the record. There is no index beside it — the store lists
the bot's prefix and reads category and name off each key. The store-backed
ports (``ports.py``) converge it; the reader (``reader.py``) composes from it;
nothing else writes it.

**Key layout.** The promotion step already stages a teclaw bot's files under
``{bot-data base}/{entity_type}_{entity_id}/{bot_id}_{publish_id}_{stage}/teclaw/{ns}/{rel}``
so the ``bot-data`` store's base resolves them. Manifest-delivered files take
the same shape with a ``_manifest`` segment where the publish stage would be::

    teclaw/{env}/bolt_data/staff_u1/bot7_manifest/teclaw/identity/RULES.md
    └────── store base ─────┘└──────── ref path (artifact) ───────────────┘

The artifact ref's ``path`` is everything after the base; the engine-relative
``rel_path`` is ``{ns}/{rel}``, and the layout says which category a path is:
``identity/<file_type>`` is an identity file,
``workspace/skills-local/<name>/…`` is a member of the local skill package
``<name>``, and any other ``workspace/…`` is a resource by its declared path.
A write whose path would read back as another category is refused, so the
layout and the record can never disagree.

**Failures raise.** A put that did not land raises before anything else
happens. A delete that did not land raises with the object still there, so a
later delete or purge finds it and tries again; the store never forgets bytes
it can still reach, because the bytes are the record. Reads cannot raise —
the plugin folds a transport failure into "absent" or an empty listing — so
nothing here gates a write on a read: a delete is issued unconditionally
(idempotent on an absent object), and a listing that came back empty is what
the composer and the ports see, which errs towards writing again, never
towards skipping a write.

**The base is part of the key.** A file written under an earlier ``bot-data``
base is not found under the current one; the next apply writes it again.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Optional

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
#: pair — so the store is keyed the same way on both the write and the read
#: side. The key prefix therefore reads ``staff_{owner}``, not the bot
#: record's ``{entity_type}_{entity_id}`` the promotion step uses; the two
#: never share a key (``_manifest`` versus a publish stage).
OWNER_ENTITY_TYPE = "staff"
#: The engine segment of the key layout. Managed files mirror the *promotion*
#: layout, ``{bot_id}_{stage}/teclaw/{ns}/{rel}``, because that is the prefix
#: the teclaw engine already pulls a bot's files from (contract R-O3); the
#: store is used by the teclaw delivery strategy alone — ARCA writes into a
#: live container and holds no platform copy — so the segment is fixed here
#: rather than parameterised.
ENGINE_LAYOUT_SEGMENT = "teclaw"
#: The categories.
CATEGORY_IDENTITY = "identity"
CATEGORY_RESOURCES = "resources"
CATEGORY_SKILLS = "skills"
#: More keys than one bot's manifest can deliver; a listing stops there.
_LIST_LIMIT = 100_000

_SKILLS_PREFIX = f"{WORKSPACE_NS}/{SKILLS_LOCAL_DIR}/"
_IDENTITY_PREFIX = f"{IDENTITY_NS}/"
_WORKSPACE_PREFIX = f"{WORKSPACE_NS}/"


class ManagedFilesStoreError(RuntimeError):
    """An object write or delete the store could not perform."""


@dataclass(frozen=True)
class ManagedFileScope:
    """Which bot a store call is about."""

    entity_type: str
    entity_id: str
    bot_id: str

    @property
    def rel_root(self) -> str:
        """The bot's prefix under the store base; every ref path starts with it."""
        return f"{self.entity_type}_{self.entity_id}/{self.bot_id}_manifest/{ENGINE_LAYOUT_SEGMENT}"


@dataclass(frozen=True)
class ManagedFile:
    """One delivered file as the composer will reference it.

    Three paths, each one segment longer than the last, for the identity file
    ``RULES.md`` of bot ``bot7`` owned by ``u1`` under the base
    ``teclaw/dev/bolt_data``:

    - ``rel_path`` — engine-relative: ``identity/RULES.md``. The layout's
      ``{ns}/{rel}``; what the category and name are read from, and what a
      port addresses a file by.
    - ``ref_path`` — store-relative: ``staff_u1/bot7_manifest/teclaw/identity/RULES.md``.
      ``scope.rel_root + "/" + rel_path``; what goes into the artifact as
      ``FileRef.path``, resolved by the engine against the ``bot-data``
      store's base.
    - ``store_key`` — absolute in the object store:
      ``teclaw/dev/bolt_data/staff_u1/bot7_manifest/teclaw/identity/RULES.md``.
      ``base + "/" + ref_path``; what the store reads and writes.

    ``digest`` and ``size_bytes`` are known when the bytes were in hand — a
    ``put`` or a ``get`` — and ``None`` from a listing, which reads keys only.
    """

    #: ``identity`` / ``resources`` / ``skills``, read off ``rel_path``.
    category: str
    #: The identity file type, the resource's declared path, or the skill
    #: package's name — what the file is called within its category.
    name: str
    rel_path: str
    ref_path: str
    store_key: str
    digest: Optional[str] = None
    size_bytes: Optional[int] = None


def digest_of(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def category_of(rel_path: str) -> Optional[str]:
    """The category the layout assigns a path, or ``None`` outside the layout."""
    if rel_path.startswith(_IDENTITY_PREFIX) and len(rel_path) > len(_IDENTITY_PREFIX):
        return CATEGORY_IDENTITY
    if rel_path.startswith(_SKILLS_PREFIX):
        name, _, rest = rel_path[len(_SKILLS_PREFIX) :].partition("/")
        return CATEGORY_SKILLS if name and rest else None
    if rel_path.startswith(_WORKSPACE_PREFIX) and len(rel_path) > len(_WORKSPACE_PREFIX):
        return CATEGORY_RESOURCES
    return None


def name_of(category: str, rel_path: str) -> str:
    """What a file is called in its category: the identity file type, the
    resource's declared path, or the skill package's name."""
    if category == CATEGORY_IDENTITY:
        return rel_path[len(_IDENTITY_PREFIX) :]
    if category == CATEGORY_SKILLS:
        return rel_path[len(_SKILLS_PREFIX) :].partition("/")[0]
    return rel_path[len(_WORKSPACE_PREFIX) :]


class ManagedFilesStore:
    """Bytes in the object store; the key layout is the record."""

    def __init__(
        self,
        *,
        object_storage: ObjectStoragePlugin,
        store_base: Callable[[], str],
    ) -> None:
        self._oss = object_storage
        # A thunk, like the composer's store coordinates: the base depends on
        # the deployment env and is read when a key is built, not at DI time.
        self._store_base = store_base

    # ── keys ─────────────────────────────────────────────────────────────

    def ref_path(self, scope: ManagedFileScope, rel_path: str) -> str:
        return f"{scope.rel_root}/{rel_path.lstrip('/')}"

    def store_key(self, scope: ManagedFileScope, rel_path: str) -> str:
        return f"{self._store_base().rstrip('/')}/{self.ref_path(scope, rel_path)}"

    def _root_key(self, scope: ManagedFileScope) -> str:
        return f"{self._store_base().rstrip('/')}/{scope.rel_root}/"

    # ── writes ───────────────────────────────────────────────────────────

    def put(
        self,
        scope: ManagedFileScope,
        *,
        category: str,
        name: str,
        rel_path: str,
        content: bytes,
    ) -> ManagedFile:
        rel_path = rel_path.lstrip("/")
        if category_of(rel_path) != category:
            # The layout is the record: a path that would read back as
            # another category (a resource declared under ``skills-local/``,
            # say) is refused rather than misfiled.
            raise ValueError(
                f"{rel_path!r} is not a {category} path under the managed-files layout"
            )
        key = self.store_key(scope, rel_path)
        if not self._oss.put_object(key, content):
            raise ManagedFilesStoreError(f"object store put failed for {key!r}")
        return ManagedFile(
            category=category,
            name=name,
            rel_path=rel_path,
            ref_path=self.ref_path(scope, rel_path),
            store_key=key,
            digest=digest_of(content),
            size_bytes=len(content),
        )

    def delete(self, scope: ManagedFileScope, *, rel_path: str) -> None:
        """Remove one file; a file that is not there is already removed.

        No existence check first: the plugin's ``delete_object`` is idempotent
        on an absent object, and a pre-check through ``list_objects`` would
        fold a transient listing failure into "not there" and skip the delete
        silently. A delete that did not land raises instead, so the
        category's write reports it and the object is still there for the
        next delete or purge to find. A caller that needs to know whether the
        file was there asks the listing it already holds.
        """
        key = self.store_key(scope, rel_path.lstrip("/"))
        if not self._oss.delete_object(key):
            raise ManagedFilesStoreError(f"object store delete failed for {key!r}")

    def purge_owner_bot(self, owner_id: str, bot_id: str) -> int:
        """``purge`` for the bot the materialisers address at ``("staff", owner)``
        — the creation job's cleanup entry point."""
        return self.purge(
            ManagedFileScope(entity_type=OWNER_ENTITY_TYPE, entity_id=owner_id, bot_id=bot_id)
        )

    def purge(self, scope: ManagedFileScope) -> int:
        """Remove every object under the bot's prefix. Returns objects removed."""
        failed: list[str] = []
        removed = 0
        for key in self._oss.list_objects(self._root_key(scope), _LIST_LIMIT):
            if self._oss.delete_object(key):
                removed += 1
            else:
                failed.append(key)
        if failed:
            for key in failed:
                logger.warning("[managed_files] object delete failed during purge: %s", key)
            raise ManagedFilesStoreError(
                f"object store delete failed for {len(failed)} object(s) during purge"
            )
        return removed

    # ── reads ────────────────────────────────────────────────────────────

    def get(
        self, scope: ManagedFileScope, *, category: str, rel_path: str
    ) -> Optional[ManagedFile]:
        """One file with its digest and size, or ``None``. Reads the object."""
        rel_path = rel_path.lstrip("/")
        if category_of(rel_path) != category:
            return None
        content = self.read_at(scope, rel_path)
        if content is None:
            return None
        return ManagedFile(
            category=category,
            name=name_of(category, rel_path),
            rel_path=rel_path,
            ref_path=self.ref_path(scope, rel_path),
            store_key=self.store_key(scope, rel_path),
            digest=digest_of(content),
            size_bytes=len(content),
        )

    def list(self, scope: ManagedFileScope, *, category: str) -> list[ManagedFile]:
        """The category's files in path order, from the keys alone."""
        root = self._root_key(scope)
        namespace = _IDENTITY_PREFIX if category == CATEGORY_IDENTITY else _WORKSPACE_PREFIX
        files: list[ManagedFile] = []
        for key in self._oss.list_objects(root + namespace, _LIST_LIMIT):
            rel_path = key[len(root) :]
            if category_of(rel_path) != category:
                continue
            files.append(
                ManagedFile(
                    category=category,
                    name=name_of(category, rel_path),
                    rel_path=rel_path,
                    ref_path=self.ref_path(scope, rel_path),
                    store_key=key,
                )
            )
        files.sort(key=lambda f: f.rel_path)
        return files

    def read(self, file: ManagedFile) -> Optional[bytes]:
        return self._oss.get_object(file.store_key)

    def read_at(self, scope: ManagedFileScope, rel_path: str) -> Optional[bytes]:
        return self._oss.get_object(self.store_key(scope, rel_path.lstrip("/")))


__all__ = [
    "BOT_DATA_STORE",
    "CATEGORY_IDENTITY",
    "CATEGORY_RESOURCES",
    "CATEGORY_SKILLS",
    "ENGINE_LAYOUT_SEGMENT",
    "IDENTITY_NS",
    "ManagedFile",
    "ManagedFileScope",
    "ManagedFilesStore",
    "ManagedFilesStoreError",
    "OWNER_ENTITY_TYPE",
    "SKILLS_LOCAL_DIR",
    "WORKSPACE_NS",
    "category_of",
    "digest_of",
    "name_of",
]
