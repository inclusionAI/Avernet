"""Persona content is stored by the platform and delivered after the container boots.

The container platform does not materialize files, so a persona cannot ride along
with ``create_bot``. These tests pin the two-step contract and the properties that
make it safe for a swarm:

1. **Store before provision, deliver after** — a persona whose content cannot be
   stored must not get a blank container, and a container must exist before its
   workspace can be written.
2. **Distinct personas coexist** — the store is content-addressed, not a per-bot
   singleton, which is why it can carry N personas where the manifest could not.
3. **Delivery reads from the store**, by digest, rather than from a local copy.

A recorded fake store stands in for the real one: the blob tree and provenance table
are the store's business and are already covered by its own suite.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import pytest

from agentclaw.community.plugins.community.agentswarm.persona_delivery import (
    PRODUCED_ORIGIN,
    deliver_persona,
    persona_file_type,
    store_persona,
)


class _Store:
    """A content-addressed store: blobs by digest, receipts by (scope, digest)."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.rows: list[dict[str, Any]] = []
        self.fail_store = False

    def store_produced(
        self, data: bytes, *, scope: Any, origin: str, modifier: str = "",
        category: str | None = None, entry_identity: str | None = None,
    ) -> Any:
        if self.fail_store:
            raise RuntimeError("store refused")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        self.blobs.setdefault(digest, data)
        self.rows.append(
            {"scope": scope, "origin": origin, "digest": digest,
             "entry_identity": entry_identity}
        )
        return type("Record", (), {"digest": digest})()

    def read(self, digest: str) -> bytes:
        return self.blobs[digest]


class _IdentityFiles:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    async def update_bot_file(
        self, entity_type: str, entity_id: str, bot_id: str, file_type: str,
        content: str, operator_id: str, engine_type: str | None = None,
        *, stage: str = "draft",
    ) -> Any:
        self.writes.append(
            {"bot_id": bot_id, "file_type": file_type, "content": content}
        )
        return None


def _scope() -> Any:
    return type("Scope", (), {"env": "dev", "entity_id": "owner-1", "bot_id": "swarm-1"})()


def _store(instructions: str = "You are an analyst.", *, key: str = "analyst"):
    store = _Store()
    return store, store_persona(
        content=store, scope=_scope(), instructions=instructions,
        engine_type="openclaw", identity_file=None, modifier="owner-1",
        persona_key=key,
    )


# ── the file a persona lands in ─────────────────────────────────────────────


@pytest.mark.unit
def test_default_identity_file_is_soul() -> None:
    assert persona_file_type(engine_type="openclaw", identity_file=None) == "SOUL.md"


@pytest.mark.unit
def test_claude_code_names_its_own_file() -> None:
    assert persona_file_type(engine_type="claude_code", identity_file=None) == "CLAUDE.md"


@pytest.mark.unit
def test_an_explicit_identity_file_wins() -> None:
    assert (
        persona_file_type(engine_type="openclaw", identity_file="AGENTS.md")
        == "AGENTS.md"
    )


# ── storing ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_storing_files_the_bytes_and_returns_the_digest() -> None:
    store, stored = _store()

    assert stored.digest in store.blobs
    assert store.blobs[stored.digest] == b"You are an analyst."
    assert store.rows[0]["origin"] == PRODUCED_ORIGIN


@pytest.mark.unit
def test_two_personas_with_different_content_coexist() -> None:
    """The reason the manifest seam could not do this: it has one document per bot."""
    store = _Store()

    a = store_persona(
        content=store, scope=_scope(), instructions="persona A", engine_type="openclaw",
        identity_file=None, modifier="o", persona_key="a",
    )
    b = store_persona(
        content=store, scope=_scope(), instructions="persona B", engine_type="openclaw",
        identity_file=None, modifier="o", persona_key="b",
    )

    assert a.digest != b.digest
    assert store.blobs[a.digest] == b"persona A"
    assert store.blobs[b.digest] == b"persona B", "B must not displace A"


@pytest.mark.unit
def test_empty_instructions_are_stored_as_an_empty_file() -> None:
    """A persona that declares nothing must land empty, not inherit a stale file."""
    store, stored = _store("")

    assert store.blobs[stored.digest] == b""
    assert stored.size_bytes == 0


@pytest.mark.unit
def test_a_store_refusal_propagates_to_the_caller() -> None:
    store = _Store()
    store.fail_store = True

    with pytest.raises(RuntimeError, match="refused"):
        store_persona(
            content=store, scope=_scope(), instructions="x", engine_type="openclaw",
            identity_file=None, modifier="o", persona_key="a",
        )


# ── delivering ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_delivery_writes_the_stored_bytes_into_the_worker() -> None:
    store, stored = _store("You are an analyst.")
    files = _IdentityFiles()

    asyncio.run(
        deliver_persona(
            content=store, identity_files=files, stored=stored,
            entity_type="staff", entity_id="owner-1", worker_id="worker-1",
            owner_id="owner-1", engine_type="openclaw",
        )
    )

    assert files.writes == [
        {
            "bot_id": "worker-1",
            "file_type": "SOUL.md",
            "content": "You are an analyst.",
        }
    ]


@pytest.mark.unit
def test_delivery_reads_the_store_rather_than_a_local_copy() -> None:
    """The digest is the contract: bytes the store no longer holds cannot be sent."""
    store, stored = _store()
    files = _IdentityFiles()
    store.blobs.pop(stored.digest)

    with pytest.raises(KeyError):
        asyncio.run(
            deliver_persona(
                content=store, identity_files=files, stored=stored,
                entity_type="staff", entity_id="owner-1", worker_id="w",
                owner_id="owner-1", engine_type="openclaw",
            )
        )
    assert files.writes == []


@pytest.mark.unit
def test_each_worker_receives_its_own_persona() -> None:
    store = _Store()
    files = _IdentityFiles()
    stored_a = store_persona(
        content=store, scope=_scope(), instructions="A", engine_type="openclaw",
        identity_file=None, modifier="o", persona_key="a",
    )
    stored_b = store_persona(
        content=store, scope=_scope(), instructions="B", engine_type="openclaw",
        identity_file=None, modifier="o", persona_key="b",
    )

    for worker, stored in (("w-a", stored_a), ("w-b", stored_b)):
        asyncio.run(
            deliver_persona(
                content=store, identity_files=files, stored=stored,
                entity_type="staff", entity_id="owner-1", worker_id=worker,
                owner_id="owner-1", engine_type="openclaw",
            )
        )

    assert files.writes[0]["content"] == "A"
    assert files.writes[1]["content"] == "B"