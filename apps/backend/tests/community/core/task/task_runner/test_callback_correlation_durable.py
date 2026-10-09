"""Durable ``CallbackCorrelationRegistry`` — REQ-P1 restart-recovery tests.

The registry's in-memory map is the routing truth for the current instance; the
optional ``TaskCallbackCorrelationRepository`` adds restart-recovery durability:
on register the correlation is ALSO persisted (best-effort), and on a resolve
cache-miss the registry falls back to the DB so a post-restart callback (arriving
by ``(source, instance_id_str)``) is still routed to the right ``(task_id,
node_id)``. These tests use a capturing fake repo (mirrors the P1b idempotent
upsert + find semantics) so the registry is exercised without DB plumbing.
"""
from __future__ import annotations

import logging

import pytest

from agentclaw.community.adapters.http.task.schemas import TaskCallbackRequest
from agentclaw.community.adapters.http.task.translator import translate
from agentclaw.community.core.task.repository.types import (
    TaskCallbackCorrelationRecord,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CorrelationRecord,
    InMemoryCallbackCorrelationRegistry,
)

_LOGGER_NAME = "task.callback_correlation"


class _FakeCorrelationRepo:
    """In-memory stand-in for ``TaskCallbackCorrelationRepositoryProtocol``.

    Mirrors the real repo's idempotent semantics: ``upsert_on_register`` returns
    the existing row UNCHANGED on a duplicate ``event_id`` (no mutation, no
    duplicate), else inserts. ``find_by_event_id`` returns the stored row or
    ``None``. Records calls for assertions. ``raise_on_upsert`` / ``raise_on_find``
    flip the respective op to raise, for best-effort swallow tests.
    """

    def __init__(
        self, *, raise_on_upsert: bool = False, raise_on_find: bool = False
    ) -> None:
        self._rows: dict[str, TaskCallbackCorrelationRecord] = {}
        self.upsert_calls: list[dict] = []
        self.find_calls: list[str] = []
        self._raise_on_upsert = raise_on_upsert
        self._raise_on_find = raise_on_find

    def upsert_on_register(
        self,
        event_id: str,
        main_session_id: str,
        task_id: str,
        node_id: str,
        retry: int,
    ) -> TaskCallbackCorrelationRecord:
        self.upsert_calls.append(
            {
                "event_id": event_id,
                "main_session_id": main_session_id,
                "task_id": task_id,
                "node_id": node_id,
                "retry": retry,
            }
        )
        if self._raise_on_upsert:
            raise RuntimeError("upsert boom")
        existing = self._rows.get(event_id)
        if existing is not None:
            # idempotent: return existing unchanged (no mutation, no duplicate)
            return existing
        rec = TaskCallbackCorrelationRecord(
            id=len(self._rows) + 1,
            event_id=event_id,
            main_session_id=main_session_id,
            task_id=task_id,
            node_id=node_id,
            retry=retry,
        )
        self._rows[event_id] = rec
        return rec

    def find_by_event_id(self, event_id: str):
        self.find_calls.append(event_id)
        if self._raise_on_find:
            raise RuntimeError("find boom")
        return self._rows.get(event_id)


def _register(reg, *, source="bcn", instance_id_str="inst77", **kw):
    kw.setdefault("workflow_id", 7)
    kw.setdefault("instance_id", 77)
    kw.setdefault("task_id", "t1")
    kw.setdefault("node_id", "n1")
    kw.setdefault("loop_task_id", "t1::n1")
    kw.setdefault("workflow_id_str", "w7")
    kw.setdefault("instance_id_str", instance_id_str)
    reg.register(source=source, **kw)
    return reg


class TestDurableRegister:
    def test_register_persists_to_db_and_memory(self):
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        _register(reg, source="bcn", instance_id_str="inst77")

        # DB write happened with the durable composite key + main_session_id.
        assert len(repo.upsert_calls) == 1
        call = repo.upsert_calls[0]
        assert call["event_id"] == "bcn:inst77"
        assert call["main_session_id"] == "inst77"
        assert call["task_id"] == "t1"
        assert call["node_id"] == "n1"
        assert call["retry"] == 0  # registry doesn't track retry; sentinel
        # In-memory map is also populated (routing truth for current instance).
        assert reg.resolve("bcn", "inst77") == CorrelationRecord(
            "t1", "n1", "t1::n1", 7, 77
        )

    def test_register_idempotent_db_row(self):
        """Duplicate register: the DB row stays ONE (P1b idempotent upsert),
        the in-memory map holds one entry (last write wins in memory)."""
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        _register(reg, source="bcn", instance_id_str="inst77")
        _register(reg, source="bcn", instance_id_str="inst77")

        assert len(repo.upsert_calls) == 2  # registry always attempts (best-effort)
        assert len(repo._rows) == 1  # idempotent: one DB row, unchanged
        rec = reg.resolve("bcn", "inst77")
        assert rec is not None
        assert rec.task_id == "t1"

    def test_persist_failure_is_best_effort(self, caplog):
        """A repo whose upsert RAISES → WARNING + in-memory STILL populated +
        NO re-raise out of register (current-instance routing unaffected)."""
        repo = _FakeCorrelationRepo(raise_on_upsert=True)
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            _register(reg, source="bcn", instance_id_str="inst77")
        # No re-raise; in-memory map still populated.
        assert reg.resolve("bcn", "inst77") == CorrelationRecord(
            "t1", "n1", "t1::n1", 7, 77
        )
        assert any(
            "upsert" in rec.message.lower() or "register" in rec.message.lower()
            for rec in caplog.records
        ), "expected a WARNING on durable-register failure"


class TestRestartRecovery:
    def test_cache_miss_db_fallback_recovers_record(self):
        """Core REQ-P1 acceptance: register → simulate restart (reconstruct the
        registry empty with the SAME repo) → resolve recovers the (task_id,
        node_id, loop_task_id) from the DB fallback."""
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        _register(reg, source="bcn", instance_id_str="inst77",
                  task_id="t9", node_id="n9", loop_task_id="t9::n9")

        # Simulate restart: fresh registry, empty memory, same durable repo.
        restarted = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        rec = restarted.resolve("bcn", "inst77")
        assert rec is not None
        assert rec.task_id == "t9"
        assert rec.node_id == "n9"
        assert rec.loop_task_id == "t9::n9"  # rederived from task_id::node_id
        # SSOT int ids are not stored in the correlation table → degrade to 0
        # (same as the translator's existing unregistered fallback).
        assert rec.workflow_id == 0
        assert rec.instance_id == 0
        # The DB fallback was actually consulted.
        assert repo.find_calls == ["bcn:inst77"]

    def test_cache_hit_does_not_hit_db(self):
        """When memory has the entry, resolve must NOT touch the DB (fast path)."""
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        _register(reg, source="bcn", instance_id_str="inst77")
        assert repo.find_calls == []  # register writes, doesn't read
        assert reg.resolve("bcn", "inst77") is not None
        assert repo.find_calls == []  # memory hit → no DB fallback

    def test_db_fallback_caches_recovered_record(self):
        """A recovered record is cached back to memory so subsequent resolves
        don't re-hit the DB."""
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        _register(reg, source="bcn", instance_id_str="inst77",
                  task_id="t2", node_id="n2", loop_task_id="t2::n2")
        restarted = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        assert restarted.resolve("bcn", "inst77") is not None
        first_find_count = len(repo.find_calls)
        # Second resolve: memory now holds the recovered record → no new DB find.
        assert restarted.resolve("bcn", "inst77") is not None
        assert len(repo.find_calls) == first_find_count

    def test_lookup_miss_db_miss_returns_none(self):
        """Unknown (source, instance_id_str) — not in memory, not in DB → None."""
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        assert reg.resolve("bcn", "never-registered") is None
        assert repo.find_calls == ["bcn:never-registered"]

    def test_lookup_db_failure_warns_and_returns_none(self, caplog):
        """DB-read failure on the fallback → WARNING + None, no raise out of
        resolve (registry is a lookup helper; a DB hiccup degrades to
        'un-correlated, ingress decides')."""
        repo = _FakeCorrelationRepo(raise_on_find=True)
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            ret = reg.resolve("bcn", "inst77")
        assert ret is None
        assert any(
            "find" in rec.message.lower() or "lookup" in rec.message.lower()
            for rec in caplog.records
        ), "expected a WARNING on DB-fallback read failure"


class TestNoRepoInjected:
    def test_none_repo_register_resolve_works_in_memory(self):
        """With no repo injected, the registry behaves exactly as before
        (original in-memory behavior); no DB op, no raise."""
        reg = InMemoryCallbackCorrelationRegistry()  # correlation_repo default None
        _register(reg, source="bcn", instance_id_str="inst77")
        assert reg.resolve("bcn", "inst77") == CorrelationRecord(
            "t1", "n1", "t1::n1", 7, 77
        )
        assert reg.resolve("bcn", "unknown") is None

    def test_none_repo_resolve_unknown_no_raise(self, caplog):
        reg = InMemoryCallbackCorrelationRegistry()
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            assert reg.resolve("bcn", "unknown") is None
        # No DB op attempted, no warning about DB (graceful None).
        assert caplog.records == []


class TestTranslatorIntegration:
    """End-to-end through the real consumer: ``translator.translate`` resolves
    the (source, instance_id_str) via the registry to recover ``loop_task_id``
    after a simulated restart."""

    def test_translate_recovers_loop_task_id_after_restart(self):
        repo = _FakeCorrelationRepo()
        reg = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        _register(reg, source="bcn", instance_id_str="i1",
                  task_id="t1", node_id="root1", loop_task_id="t1::root1")
        # Simulate restart: fresh registry, empty memory, same durable repo.
        restarted = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)

        req = TaskCallbackRequest(
            task_id="t1", workflow_source="bcn", workflow_id="w7",
            workflow_instance_id="i1", status="COMPLETED", is_success=True,
            # No loop_task_id → translator must resolve via the registry.
        )
        tc = translate(req, "result", restarted)
        # loop_task_id recovered from the DB fallback (routing still works).
        assert tc.data.data["loop_task_id"] == "t1::root1"

    def test_translate_unregistered_after_restart_raises_not_found(self):
        """A callback whose correlation was never registered (not in memory nor
        DB) still raises NotFound — the DB fallback preserves the ingress's
        existing not-found semantics, it does not invent a correlation."""
        from agentclaw.community.core.errors import NotFound

        repo = _FakeCorrelationRepo()
        restarted = InMemoryCallbackCorrelationRegistry(correlation_repo=repo)
        req = TaskCallbackRequest(
            task_id="t1", workflow_source="bcn", workflow_id="w7",
            workflow_instance_id="ghost", status="COMPLETED", is_success=True,
        )
        with pytest.raises(NotFound):
            translate(req, "result", restarted)
