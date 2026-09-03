"""The platform's own copy of a bot's CLI-tool bytes (W9, issue #1477).

**Why a copy exists at all.** On teclaw the composed artifact *is* the
delivery, and an artifact composed for a live CLI update or a manifest apply
has to reference the tool *now*. Gathering the bytes from the engine at that
moment would be circular: the platform is the side that just fetched, pinned
and verified them, and on a manifest-driven creation there is no container to
gather from yet. So the bytes are written here at install time, and every
later consumer — the artifact refs, a publish-stage snapshot, a re-delivery
after the source URL rotated — reads the platform's copy (spec D-4).

**Key layout.** Two prefixes under the ``bot-data`` store's base, both keyed
by the bot, for a tool named ``mycli`` on bot ``bot7`` owned by ``staff u1``::

    live     teclaw/dev/bolt_data/staff_u1/bot7_cli/mycli
    staged   teclaw/dev/bolt_data/staff_u1/bot7_9_verify/teclaw/cli/mycli
             └────────── store base ─────┘└──────── ref path ─────────┘

The live prefix holds what the bot has installed *now*; it is not a snapshot
of anything and so carries no engine-layout segment. The staged prefix is the
one ``TeclawFilePromotion`` already builds — ``{bot}_{publish_id}_{stage}``,
then the engine segment, then a namespace — with ``cli`` in the namespace slot
beside ``workspace`` and ``identity``. Stage-scoping is what keeps a draft and
a verify snapshot from sharing objects, so republishing a draft cannot change
what a published bot runs. Note the segment lives in an *object key*, not in a
container path: the engine resolves a ``cliToolRef`` and decides placement
itself, so nothing here says where the tool lands.

**A recorded key is addressed by the key, not recomputed.** ``ac_bot_cli_tool``
stores ``oss_key`` per row, and the base it was written under can change
between deploys. ``delete`` and ``copy_to_stage`` therefore take the key the
row holds; only ``put`` builds one.

**Failures raise.** A put, copy or delete that did not land raises before
anything else happens, so a row is never recorded for bytes that are not
there, and an object is never forgotten while it is still reachable. Reads
cannot raise — the plugin folds a transport failure into "absent" — so the
one read here, the copy fallback's, treats an absent source as a failure
rather than staging an empty tool.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import (
    ObjectCopyCapability,
    ObjectStoragePlugin,
)

logger = get_logger()

#: The composer store id whose base is the teclaw bot-data prefix. A
#: ``cliToolRef``'s ``store`` field carries it, exactly as a ``FileRef``'s does.
BOT_DATA_STORE = "bot-data"
#: The namespace slot a staged tool occupies, beside ``workspace`` / ``identity``.
CLI_NS = "cli"
#: The engine segment of the promotion layout (``…/{stage}/teclaw/{ns}/…``).
ENGINE_LAYOUT_SEGMENT = "teclaw"
#: What distinguishes the live prefix from a publish stage's.
LIVE_SEGMENT_SUFFIX = "_cli"

#: A tool name is one key segment and nothing else. The schema's ``check_name``
#: (``schema/_support.py``) is the authoritative validation and the service
#: applies it; this is the store refusing to build a key it cannot vouch for,
#: because a name reaching here from a future caller must not be able to choose
#: a prefix. Same rule, deliberately duplicated at the boundary that uses it.
_KEY_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: ``ac_bot_cli_tool.name``'s column width, restated here because it is also
#: what keeps a key inside the object store's own 1024-byte key cap: the
#: prefixes above are bounded, and this is the one segment a caller chooses.
MAX_NAME_LENGTH = 128


class CliToolStoreError(RuntimeError):
    """An object write, copy or delete the store could not perform."""


@dataclass(frozen=True)
class CliToolScope:
    """Which bot a store call is about."""

    entity_type: str
    entity_id: str
    bot_id: str

    @property
    def rel_root(self) -> str:
        """The bot's live prefix under the store base."""
        return f"{self.entity_type}_{self.entity_id}/{self.bot_id}{LIVE_SEGMENT_SUFFIX}"

    def stage_rel_root(self, *, publish_id: int, stage: str) -> str:
        """The bot's prefix for one publish stage, in the promotion layout."""
        return (
            f"{self.entity_type}_{self.entity_id}/"
            f"{self.bot_id}_{publish_id}_{stage}/{ENGINE_LAYOUT_SEGMENT}/{CLI_NS}"
        )


@dataclass(frozen=True)
class StoredCliTool:
    """One tool's bytes as the platform addresses them.

    - ``ref_path`` — store-relative, what a ``cliToolRef``'s ``path`` carries
      and what the engine resolves against the ``bot-data`` store's base.
    - ``store_key`` — absolute in the object store: ``base + "/" + ref_path``.
      What the row records and what a later delete or copy addresses.
    """

    name: str
    ref_path: str
    store_key: str
    #: The store id a ref pairs with ``ref_path``; constant, carried so a
    #: caller composing a ref needs nothing but this object.
    store: str = BOT_DATA_STORE


def checked_name(name: str) -> str:
    """The name, or ``ValueError`` if it cannot be one key segment."""
    if not isinstance(name, str) or not _KEY_SAFE_NAME_RE.match(name):
        raise ValueError(
            f"{name!r} is not a usable CLI tool name: it must start with a "
            "letter or digit and contain only letters, digits, '.', '_' and '-'"
        )
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(
            f"CLI tool name is {len(name)} characters; the column and the "
            f"object key both cap it at {MAX_NAME_LENGTH}"
        )
    return name


class CliToolStore:
    """Bytes in the object store; the metadata table is the record beside them."""

    def __init__(
        self,
        *,
        object_storage: ObjectStoragePlugin,
        store_base: Callable[[], str],
    ) -> None:
        self._oss = object_storage
        # A thunk, like the composer's store coordinates and W8's managed-files
        # store: the base depends on the deployment env and is read when a key
        # is built, not at DI time.
        self._store_base = store_base

    # ── keys ─────────────────────────────────────────────────────────────

    def _base(self) -> str:
        return self._store_base().rstrip("/")

    def ref_path(self, scope: CliToolScope, name: str) -> str:
        return f"{scope.rel_root}/{checked_name(name)}"

    def store_key(self, scope: CliToolScope, name: str) -> str:
        return f"{self._base()}/{self.ref_path(scope, name)}"

    def stage_ref_path(
        self, scope: CliToolScope, *, name: str, publish_id: int, stage: str
    ) -> str:
        root = scope.stage_rel_root(publish_id=publish_id, stage=stage)
        return f"{root}/{checked_name(name)}"

    def stage_store_key(
        self, scope: CliToolScope, *, name: str, publish_id: int, stage: str
    ) -> str:
        ref = self.stage_ref_path(
            scope, name=name, publish_id=publish_id, stage=stage
        )
        return f"{self._base()}/{ref}"

    # ── writes ───────────────────────────────────────────────────────────

    def put(self, scope: CliToolScope, *, name: str, data: bytes) -> StoredCliTool:
        """Write a tool's bytes to the live prefix; return what to record."""
        ref_path = self.ref_path(scope, name)
        key = f"{self._base()}/{ref_path}"
        if not self._oss.put_object(key, data):
            raise CliToolStoreError(f"object store put failed for {key!r}")
        return StoredCliTool(name=name, ref_path=ref_path, store_key=key)

    def copy_to_stage(
        self,
        scope: CliToolScope,
        *,
        name: str,
        source_key: str,
        publish_id: int,
        stage: str,
    ) -> StoredCliTool:
        """Duplicate a recorded object into one publish stage's prefix.

        ``source_key`` is the row's ``oss_key`` rather than a recomputed live
        key, so a tool written under an earlier store base still promotes.

        Server-side where the store offers it, which is the point: a tool can
        be hundreds of megabytes and promotion copies every one of a bot's.
        Where it does not, the bytes are read and written back — slower, and
        the only reason this is a capability rather than a requirement, since
        an overlay that has not shipped ``copy_object`` must still be able to
        promote a bot.
        """
        ref_path = self.stage_ref_path(
            scope, name=name, publish_id=publish_id, stage=stage
        )
        dest_key = f"{self._base()}/{ref_path}"
        if isinstance(self._oss, ObjectCopyCapability):
            if not self._oss.copy_object(source_key, dest_key):
                raise CliToolStoreError(
                    f"object store copy failed for {source_key!r} -> {dest_key!r}"
                )
        else:
            logger.info(
                "[cli_tools] object store has no server-side copy; staging %r "
                "through the backend",
                source_key,
            )
            content = self._oss.get_object(source_key)
            if content is None:
                # Absent or unreadable — the plugin folds both into None. Either
                # way there is nothing to stage, and writing an empty object
                # would put a broken tool in a published artifact.
                raise CliToolStoreError(
                    f"object store read failed for {source_key!r}; nothing staged"
                )
            if not self._oss.put_object(dest_key, content):
                raise CliToolStoreError(f"object store put failed for {dest_key!r}")
        return StoredCliTool(name=name, ref_path=ref_path, store_key=dest_key)

    def delete(self, *, key: str) -> None:
        """Remove one recorded object; one that is not there is already removed.

        No existence check first: ``delete_object`` is idempotent on an absent
        object, and a pre-check through a listing would fold a transient
        failure into "not there" and skip the delete silently. A delete that
        did not land raises with the object still present, so the next removal
        finds it and tries again.
        """
        if not self._oss.delete_object(key):
            raise CliToolStoreError(f"object store delete failed for {key!r}")


__all__ = [
    "BOT_DATA_STORE",
    "CLI_NS",
    "MAX_NAME_LENGTH",
    "ENGINE_LAYOUT_SEGMENT",
    "LIVE_SEGMENT_SUFFIX",
    "CliToolScope",
    "CliToolStore",
    "CliToolStoreError",
    "StoredCliTool",
    "checked_name",
]
