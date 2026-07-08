"""ConcurrencyLimiter / BotQpmManager / MachineCountProvider 单元测试（阶段一）。"""

from __future__ import annotations

import time

from secbaas.core.service.bot_run._bot_concurrency import (
    BotConcurrencyManager,
    ConcurrencyLimiter,
    FixedMachineCountProvider,
)

# ----------------------------- ConcurrencyLimiter -----------------------------


def test_limiter_acquire_and_release():
    limiter = ConcurrencyLimiter(capacity=2)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False  # 槽位已满
    limiter.release()
    assert limiter.try_acquire() is True  # 归还后可再次获取


def test_limiter_has_slot():
    limiter = ConcurrencyLimiter(capacity=1)
    assert limiter.has_slot() is True
    assert limiter.try_acquire() is True
    assert limiter.has_slot() is False


def test_limiter_ref_count():
    limiter = ConcurrencyLimiter(capacity=3)
    assert limiter.ref_count == 0
    limiter.try_acquire()
    assert limiter.ref_count == 1
    limiter.try_acquire()
    assert limiter.ref_count == 2
    limiter.release()
    assert limiter.ref_count == 1


# ----------------------- FixedMachineCountProvider ----------------------


def test_machine_count_floor_one():
    assert FixedMachineCountProvider(0).get_machine_count() == 1
    assert FixedMachineCountProvider(3).get_machine_count() == 3


# ----------------------------- BotQpmManager ----------------------------


class _FakeRepo:
    def __init__(self, mapping=None, fail=False):
        self._mapping = mapping or {}
        self._fail = fail
        self.calls = 0

    def list_all(self):
        self.calls += 1
        if self._fail:
            raise RuntimeError("db down")
        from secbaas.core.repository.bot_qpm import BotQpmRecord

        return [
            BotQpmRecord(
                id=i,
                bot_id=b,
                qpm=q,
                env="test",
                gmt_create=None,
                gmt_modified=None,
            )
            for i, (b, q) in enumerate(self._mapping.items())
        ]

    def get_by_bot_id(self, bot_id):  # pragma: no cover - unused here
        return None

    def upsert(self, *, bot_id, qpm):  # pragma: no cover - unused here
        self._mapping[bot_id] = qpm


def test_get_qpm_default_when_unconfigured():
    mgr = BotConcurrencyManager(_FakeRepo({}))
    assert mgr.get_concurrency_num("bot-unknown") is None


def test_get_qpm_configured():
    mgr = BotConcurrencyManager(_FakeRepo({"bot-1": 120}))
    assert mgr.get_concurrency_num("bot-1") == 120
    assert mgr.get_concurrency_num("bot-2") is None


def test_refresh_not_called_within_interval():
    repo = _FakeRepo({"bot-1": 10})
    mgr = BotConcurrencyManager(repo, refresh_interval_seconds=999)
    mgr.get_concurrency_num("bot-1")
    mgr.get_concurrency_num("bot-1")
    mgr.get_concurrency_num("bot-1")
    assert repo.calls == 1  # 间隔内只刷新一次


def test_refresh_picks_up_changes_after_interval():
    repo = _FakeRepo({"bot-1": 10})
    mgr = BotConcurrencyManager(repo, refresh_interval_seconds=0.0)
    assert mgr.get_concurrency_num("bot-1") == 10
    repo._mapping["bot-1"] = 99
    time.sleep(0.001)
    assert mgr.get_concurrency_num("bot-1") == 99  # 热更新生效


def test_refresh_failure_keeps_stale_cache():
    repo = _FakeRepo({"bot-1": 10}, fail=False)
    mgr = BotConcurrencyManager(repo, refresh_interval_seconds=0.0)
    assert mgr.get_concurrency_num("bot-1") == 10  # 首次加载成功
    repo._fail = True
    time.sleep(0.001)
    # 刷新失败，保留旧缓存而非崩溃或清空
    assert mgr.get_concurrency_num("bot-1") == 10
