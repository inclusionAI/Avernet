"""Fakes shared by the CLI-tools tests (W9)."""
from __future__ import annotations

from typing import Optional


class FakeObjectStorage:
    """A dict-backed ``ObjectStoragePlugin`` with **no** server-side copy.

    Deliberately without ``copy_object``: this is the shape of an overlay that
    has not shipped :class:`ObjectCopyCapability`, and it is what exercises the
    store's read-through staging path.
    """

    def __init__(self, *, fail_puts: bool = False, fail_deletes: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.reads: list[str] = []
        self.fail_puts = fail_puts
        self.fail_deletes = fail_deletes

    def put_object(self, key: str, content) -> bool:
        if self.fail_puts:
            return False
        self.puts.append(key)
        self.objects[key] = content if isinstance(content, bytes) else content.encode()
        return True

    def get_object(self, key: str) -> Optional[bytes]:
        self.reads.append(key)
        return self.objects.get(key)

    def delete_object(self, key: str) -> bool:
        if self.fail_deletes:
            return False
        # The plugin contract: an already-absent object deletes successfully.
        self.deletes.append(key)
        self.objects.pop(key, None)
        return True

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))[:max_keys]


class FakeCopyingObjectStorage(FakeObjectStorage):
    """The same store, plus :class:`ObjectCopyCapability`."""

    def __init__(self, *, fail_copies: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.copies: list[tuple[str, str]] = []
        self.fail_copies = fail_copies

    def copy_object(self, source_key: str, dest_key: str) -> bool:
        if self.fail_copies or source_key not in self.objects:
            return False
        self.copies.append((source_key, dest_key))
        self.objects[dest_key] = self.objects[source_key]
        return True


# ── the service's collaborators ───────────────────────────────────────────


class FakeCliToolRepo:
    """An in-memory ``BotCliToolRepositoryProtocol``, scoped like the real one."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], object] = {}
        self.deletes: list[str] = []

    @staticmethod
    def _key(env, entity_id, bot_id, name):
        return (env, entity_id, bot_id, name)

    def get(self, *, env, entity_id, bot_id, name):
        return self.rows.get(self._key(env, entity_id, bot_id, name))

    def list(self, *, env, entity_id, bot_id):
        rows = [
            row for (e, ent, b, _), row in self.rows.items()
            if (e, ent, b) == (env, entity_id, bot_id)
        ]
        return sorted(rows, key=lambda r: r.name)

    def upsert(self, **kwargs):
        from agentclaw.community.core.bot_config_manifest.cli_tools import (
            BotCliToolRecord,
        )

        record = BotCliToolRecord(id=len(self.rows) + 1, **kwargs)
        self.rows[
            self._key(kwargs["env"], kwargs["entity_id"], kwargs["bot_id"], kwargs["name"])
        ] = record
        return record

    def delete(self, *, env, entity_id, bot_id, name) -> bool:
        self.deletes.append(name)
        return self.rows.pop(self._key(env, entity_id, bot_id, name), None) is not None

    def delete_all(self, *, env, entity_id, bot_id):
        keys = []
        for composite in list(self.rows):
            if composite[:3] == (env, entity_id, bot_id):
                keys.append(self.rows.pop(composite).oss_key)
        return keys


class FakeDelivery:
    """Records every delivery call; refuses on demand."""

    def __init__(self, *, install_error=None, delete_error=None, listing=None) -> None:
        self.installed: list[tuple[str, bytes]] = []
        self.deleted: list[str] = []
        self.listed = 0
        self.install_error = install_error
        self.delete_error = delete_error
        self.listing = listing

    async def install(self, ctx, *, name, data) -> None:
        if self.install_error is not None:
            raise self.install_error
        self.installed.append((name, data))

    async def delete(self, ctx, *, name) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)

    async def list(self, ctx):
        self.listed += 1
        if isinstance(self.listing, Exception):
            raise self.listing
        return list(self.listing or [])


class FakeFetchedEntry:
    def __init__(self, content: bytes, digest: str) -> None:
        self.content = content
        self.digest = digest
        self.from_store = False
        self.fallback_reason = None
        self.source_url = None


class FakeEntryFetcher:
    """Answers with canned bytes; records the keyword arguments it was given."""

    def __init__(self, *, content: bytes = b"", digest: str = "", error=None) -> None:
        self.content = content
        self.digest = digest
        self.error = error
        self.calls: list[dict] = []

    def fetch(self, ctx, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeFetchedEntry(self.content, self.digest or kwargs.get("digest") or "")


def code_of(module) -> str:
    """The module's source with comments and string literals removed.

    Prose is not the subject of the source-scan tests — a docstring may
    perfectly well say "this service branches on no engine type", and
    asserting over raw source would make the sentence that documents the rule
    the thing that breaks it. What must stay absent is a *call*, a *branch* and
    a *constant*.
    """
    import inspect
    import io
    import tokenize

    kept: list[str] = []
    for token in tokenize.generate_tokens(
        io.StringIO(inspect.getsource(module)).readline
    ):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(token.string)
    return " ".join(kept)
