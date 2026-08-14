"""Unit tests for CollaboratorLockService.

Tests the service layer logic with mocked repositories.
"""
import pytest
from unittest.mock import MagicMock, Mock
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
    LockNotHeldError,
    LockReleaseDeniedError,
)
from agentclaw.community.core.bot_collaborator.models import BotCollabLockRecord


@pytest.fixture
def lock_repo():
    """Create a mock lock repository."""
    return Mock()


@pytest.fixture
def collab_service():
    """Create a mock collaborator service."""
    return Mock()


@pytest.fixture
def bot_service():
    """Create a mock bot service."""
    return Mock()


@pytest.fixture
def service(lock_repo, collab_service, bot_service):
    """Create a CollaboratorLockService with mocked dependencies."""
    return CollaboratorLockService(
        lock_repo=lock_repo,
        collab_service=collab_service,
        bot_service=bot_service,
    )


def _make_lock_record(bot_id: str, owner_id: str, holder_user_id: str) -> BotCollabLockRecord:
    """Helper to create a BotCollabLockRecord."""
    lock_key = f"{bot_id}:{owner_id}"
    return BotCollabLockRecord(
        id=1,
        lock_key=lock_key,
        holder_user_id=holder_user_id,
        env="dev",
        gmt_create="2024-01-01T00:00:00",
        gmt_modified="2024-01-01T00:00:00",
    )


# --- acquire_lock tests -----------------------------------------------------

def test_acquire_lock_success(service, lock_repo):
    """Test successful lock acquisition."""
    lock_repo.get_by_key.return_value = None  # No existing lock
    lock_repo.acquire.return_value = _make_lock_record("bot-123", "owner-001", "user-001")

    lock = service.acquire_lock("bot-123", "owner-001", "user-001")

    assert lock is not None
    assert lock.lock_key == "bot-123:owner-001"
    assert lock.holder_user_id == "user-001"
    lock_repo.acquire.assert_called_once_with("bot-123:owner-001", "user-001")


def test_acquire_lock_reentrant(service, lock_repo):
    """Test lock acquisition with reentrant - same user already holds the lock."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-001")

    lock = service.acquire_lock("bot-123", "owner-001", "user-001")

    assert lock is not None
    assert lock.holder_user_id == "user-001"
    # Should NOT try to acquire, just return existing lock
    lock_repo.acquire.assert_not_called()


def test_acquire_lock_already_held_by_other(service, lock_repo):
    """Test lock acquisition returns None when held by another user."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-002")

    lock = service.acquire_lock("bot-123", "owner-001", "user-001")

    assert lock is None
    # Should NOT try to acquire
    lock_repo.acquire.assert_not_called()


def test_acquire_lock_concurrent_conflict(service, lock_repo):
    """Test lock acquisition returns None on concurrent conflict (IntegrityError)."""
    lock_repo.get_by_key.return_value = None  # No existing lock initially
    lock_repo.acquire.side_effect = IntegrityError("", "", "")

    lock = service.acquire_lock("bot-123", "owner-001", "user-001")

    assert lock is None


# --- release_lock tests -----------------------------------------------------

def test_release_lock_success(service, lock_repo):
    """Test successful lock release by holder."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-001")
    lock_repo.release.return_value = True

    result = service.release_lock("bot-123", "owner-001", "user-001")

    assert result is True
    lock_repo.release.assert_called_once_with("bot-123:owner-001")


def test_release_lock_not_held(service, lock_repo):
    """Test lock release fails when lock not held."""
    lock_repo.get_by_key.return_value = None

    with pytest.raises(LockNotHeldError) as exc_info:
        service.release_lock("bot-123", "owner-001", "user-001")

    assert exc_info.value.bot_id == "bot-123"
    assert exc_info.value.owner_id == "owner-001"


def test_release_lock_denied_for_non_holder(service, lock_repo):
    """Test lock release denied for non-holder."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-001")

    with pytest.raises(LockReleaseDeniedError) as exc_info:
        service.release_lock("bot-123", "owner-001", "user-002")

    assert exc_info.value.bot_id == "bot-123"
    assert exc_info.value.owner_id == "owner-001"
    assert exc_info.value.holder_user_id == "user-001"
    assert exc_info.value.requester_user_id == "user-002"


def test_release_lock_force_by_non_holder(service, lock_repo):
    """Test forced lock release by non-holder."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-001")
    lock_repo.release.return_value = True

    result = service.release_lock("bot-123", "owner-001", "user-002", force=True)

    assert result is True
    lock_repo.release.assert_called_once_with("bot-123:owner-001")


# --- get_lock_info tests ----------------------------------------------------

def test_get_lock_info_no_collaborators_returns_early(service, lock_repo, collab_service, bot_service):
    """Test get_lock_info returns early when no collaborators, without checking lock."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "owner-001")
    bot_service.get_bot.return_value = {"owner_name": "Owner Name"}
    collab_service.list_collaborators.return_value = []  # No collaborators

    result = service.get_lock_info("bot-123", "owner-001", "user-001")

    # When no collaborators, returns early without checking lock
    assert result.lock is None
    assert result.holder_name is None
    assert result.has_collaborators is False
    # Should NOT call lock_repo or bot_service when no collaborators
    lock_repo.get_by_key.assert_not_called()
    bot_service.get_bot.assert_not_called()


def test_get_lock_info_returns_record_with_owner_name(service, lock_repo, collab_service, bot_service):
    """Test get_lock_info returns lock record with owner name when holder is owner."""
    collab = Mock()
    collab.user_id = "user-002"
    collab.user_name = "Collaborator"
    collab_service.list_collaborators.return_value = [collab]  # Has collaborators
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "owner-001")
    bot_service.get_bot.return_value = {"owner_name": "Owner Name"}

    result = service.get_lock_info("bot-123", "owner-001", "user-001")

    assert result.lock is not None
    assert result.lock.lock_key == "bot-123:owner-001"
    assert result.holder_name == "Owner Name"
    assert result.has_collaborators is True
    bot_service.get_bot.assert_called_once_with("bot-123", "owner-001")


def test_get_lock_info_returns_record_with_collab_name(service, lock_repo, collab_service):
    """Test get_lock_info returns lock record with collaborator name when holder is collaborator."""
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-002")
    # Mock collaborator list
    collab = Mock()
    collab.user_id = "user-002"
    collab.user_name = "Collaborator Name"
    collab_service.list_collaborators.return_value = [collab]

    result = service.get_lock_info("bot-123", "owner-001", "user-001")

    assert result.lock is not None
    assert result.lock.lock_key == "bot-123:owner-001"
    assert result.holder_name == "Collaborator Name"
    assert result.has_collaborators is True
    collab_service.list_collaborators.assert_called_once_with(
        bot_id="bot-123",
        owner_id="owner-001",
        user_id="user-001",
    )


def test_get_lock_info_returns_none(service, lock_repo, collab_service):
    """Test get_lock_info returns LockInfoResult with None lock when not held."""
    lock_repo.get_by_key.return_value = None
    collab_service.list_collaborators.return_value = []  # No collaborators

    result = service.get_lock_info("bot-123", "owner-001", "user-001")

    assert result.lock is None
    assert result.holder_name is None
    assert result.has_collaborators is False


def test_get_lock_info_has_collaborators_true(service, lock_repo, collab_service):
    """Test get_lock_info returns has_collaborators=True when bot has collaborators."""
    lock_repo.get_by_key.return_value = None
    # Mock collaborator list with one collaborator
    collab = Mock()
    collab.user_id = "user-002"
    collab.user_name = "Collaborator"
    collab_service.list_collaborators.return_value = [collab]

    result = service.get_lock_info("bot-123", "owner-001", "user-001")

    assert result.lock is None
    assert result.has_collaborators is True


def test_get_lock_info_has_collaborators_false(service, lock_repo, collab_service):
    """Test get_lock_info returns has_collaborators=False when bot has no collaborators."""
    lock_repo.get_by_key.return_value = None
    collab_service.list_collaborators.return_value = []  # No collaborators

    result = service.get_lock_info("bot-123", "owner-001", "user-001")

    assert result.lock is None
    assert result.has_collaborators is False

    assert result.lock is None
    assert result.holder_name is None


# --- _make_lock_key tests ---------------------------------------------------

def test_make_lock_key():
    """Test lock key generation."""
    key = CollaboratorLockService._make_lock_key("bot-123", "owner-001")
    assert key == "bot-123:owner-001"

    key = CollaboratorLockService._make_lock_key("my-bot", "user-456")
    assert key == "my-bot:user-456"


# --- steal_lock tests -------------------------------------------------------

def test_steal_lock_success_when_not_held(service, lock_repo):
    """Test steal_lock succeeds when lock is not held."""
    lock_repo.get_by_key.return_value = None  # No existing lock
    lock_repo.acquire.return_value = _make_lock_record("bot-123", "owner-001", "user-001")

    lock = service.steal_lock("bot-123", "owner-001", "user-001")

    assert lock is not None
    assert lock.lock_key == "bot-123:owner-001"
    assert lock.holder_user_id == "user-001"
    lock_repo.acquire.assert_called_once_with("bot-123:owner-001", "user-001")
    # Should NOT call release when no existing lock
    lock_repo.release.assert_not_called()


def test_steal_lock_steals_from_other_user(service, lock_repo):
    """Test steal_lock steals lock from another user."""
    # Existing lock held by user-002
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-002")
    lock_repo.release.return_value = True
    lock_repo.acquire.return_value = _make_lock_record("bot-123", "owner-001", "user-001")

    lock = service.steal_lock("bot-123", "owner-001", "user-001")

    assert lock is not None
    assert lock.holder_user_id == "user-001"
    # Should release the existing lock first
    lock_repo.release.assert_called_once_with("bot-123:owner-001")
    # Then acquire new lock
    lock_repo.acquire.assert_called_once_with("bot-123:owner-001", "user-001")


def test_steal_lock_steals_own_lock(service, lock_repo):
    """Test steal_lock works when user already holds the lock (re-acquire)."""
    # Existing lock held by same user
    lock_repo.get_by_key.return_value = _make_lock_record("bot-123", "owner-001", "user-001")
    lock_repo.release.return_value = True
    lock_repo.acquire.return_value = _make_lock_record("bot-123", "owner-001", "user-001")

    lock = service.steal_lock("bot-123", "owner-001", "user-001")

    assert lock is not None
    assert lock.holder_user_id == "user-001"
    # Should release the existing lock first
    lock_repo.release.assert_called_once_with("bot-123:owner-001")
    # Then acquire new lock
    lock_repo.acquire.assert_called_once_with("bot-123:owner-001", "user-001")


# --- single-box harden: real in-mem SQLite reentrant/release e2e ------------
# 上面用 mock repo 验逻辑分支。本节用真 in-mem SQLite + 真 BotCollabLockRepository
# 跑通单实例互斥语义（重入/他人挡/释放后可抢），把「local 真能跑」钉死。
# 跨进程真并发不在此覆盖：local StaticPool 单连接物理不可复现，记豁免（见 06-md）。
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.bot.collab_lock import BotCollabLockRepository


class _InMemSqliteDB:
    def __init__(self, engine):
        self._sf = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._sf()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def real_lock_service(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from agentclaw.community.core.base import Base
    Base.metadata.create_all(engine)
    return CollaboratorLockService(
        lock_repo=BotCollabLockRepository(_InMemSqliteDB(engine)),
        collab_service=MagicMock(),
        bot_service=MagicMock(),
    )


def test_reentrant_then_release_on_real_sqlite(real_lock_service):
    svc = real_lock_service
    lock1 = svc.acquire_lock("botX", "ownerX", "userA")
    assert lock1 is not None
    # 同用户重入：返回锁、不冲突
    assert svc.acquire_lock("botX", "ownerX", "userA") is not None
    # 他人抢：被单实例互斥挡掉
    assert svc.acquire_lock("botX", "ownerX", "userB") is None
    # 持有者释放成功
    assert svc.release_lock("botX", "ownerX", "userA") is True
    # 释放后可被他人抢
    assert svc.acquire_lock("botX", "ownerX", "userB") is not None


# ============================================================================
# 会话级锁（coding 应用）—— key 为 3 段 session:{bot_id}:{owner_id}:{session_id}
# 覆盖暂存区新增的 acquire/release/steal/get_session_lock_info + _make_session_lock_key
# ============================================================================

SESSION_KEY = "session:bot-123:owner-001:sess-abc"


def _make_session_lock_record(holder_user_id: str) -> BotCollabLockRecord:
    """Helper：构造一条 3 段 key 的会话锁记录。"""
    return BotCollabLockRecord(
        id=1,
        lock_key=SESSION_KEY,
        holder_user_id=holder_user_id,
        env="dev",
        gmt_create="2024-01-01T00:00:00",
        gmt_modified="2024-01-01T00:00:00",
    )


# --- _make_session_lock_key -------------------------------------------------

def test_make_session_lock_key():
    """会话锁 key 为 3 段，与 bot 级 2 段 key 命名空间隔离。"""
    key = CollaboratorLockService._make_session_lock_key("bot-123", "owner-001", "sess-abc")
    assert key == "session:bot-123:owner-001:sess-abc"
    # 与 bot 级 2 段 key 不冲突
    assert key != CollaboratorLockService._make_lock_key("bot-123", "owner-001")


# --- acquire_session_lock ---------------------------------------------------

def test_acquire_session_lock_success(service, lock_repo):
    """无锁 -> 获取成功，按 3 段 key 申请。"""
    lock_repo.get_by_key.return_value = None
    lock_repo.acquire.return_value = _make_session_lock_record("user-001")

    lock = service.acquire_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is not None
    assert lock.lock_key == SESSION_KEY
    lock_repo.acquire.assert_called_once_with(SESSION_KEY, "user-001")


def test_acquire_session_lock_reentrant(service, lock_repo):
    """自己已持有 -> 重入返回原锁，不再 acquire。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-001")

    lock = service.acquire_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is not None
    assert lock.holder_user_id == "user-001"
    lock_repo.acquire.assert_not_called()


def test_acquire_session_lock_held_by_other(service, lock_repo):
    """他人持有 -> 返回 None，不 acquire。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-002")

    lock = service.acquire_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is None
    lock_repo.acquire.assert_not_called()


def test_acquire_session_lock_concurrent_conflict(service, lock_repo):
    """并发冲突（IntegrityError）-> 返回 None。"""
    lock_repo.get_by_key.return_value = None
    lock_repo.acquire.side_effect = IntegrityError("", "", "")

    lock = service.acquire_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is None


def test_acquire_session_lock_unexpected_error(service, lock_repo):
    """acquire 抛非 IntegrityError 异常 -> 兜底返回 None，不向上抛。"""
    lock_repo.get_by_key.return_value = None
    lock_repo.acquire.side_effect = RuntimeError("db down")

    lock = service.acquire_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is None


# --- release_session_lock ---------------------------------------------------

def test_release_session_lock_no_lock_returns_true(service, lock_repo):
    """锁不存在 -> 视为已释放，返回 True，不调用 release。"""
    lock_repo.get_by_key.return_value = None

    result = service.release_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert result is True
    lock_repo.release.assert_not_called()


def test_release_session_lock_by_holder(service, lock_repo):
    """持有者释放成功。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-001")
    lock_repo.release.return_value = True

    result = service.release_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert result is True
    lock_repo.release.assert_called_once_with(SESSION_KEY)


def test_release_session_lock_denied_for_non_holder(service, lock_repo):
    """非持有者且非 force -> 抛 LockReleaseDeniedError。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-001")

    with pytest.raises(LockReleaseDeniedError) as exc_info:
        service.release_session_lock("bot-123", "owner-001", "sess-abc", "user-002")

    assert exc_info.value.holder_user_id == "user-001"
    assert exc_info.value.requester_user_id == "user-002"
    lock_repo.release.assert_not_called()


def test_release_session_lock_force_by_non_holder(service, lock_repo):
    """force=True -> 非持有者也能强制释放。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-001")
    lock_repo.release.return_value = True

    result = service.release_session_lock(
        "bot-123", "owner-001", "sess-abc", "user-002", force=True
    )

    assert result is True
    lock_repo.release.assert_called_once_with(SESSION_KEY)


# --- steal_session_lock -----------------------------------------------------

def test_steal_session_lock_when_not_held(service, lock_repo):
    """无锁时抢锁 -> 直接 acquire，不调用 release。"""
    lock_repo.get_by_key.return_value = None
    lock_repo.acquire.return_value = _make_session_lock_record("user-001")

    lock = service.steal_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is not None
    assert lock.holder_user_id == "user-001"
    lock_repo.release.assert_not_called()
    lock_repo.acquire.assert_called_once_with(SESSION_KEY, "user-001")


def test_steal_session_lock_from_other(service, lock_repo):
    """他人持有时抢锁 -> 先 release 再 acquire。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-002")
    lock_repo.release.return_value = True
    lock_repo.acquire.return_value = _make_session_lock_record("user-001")

    lock = service.steal_session_lock("bot-123", "owner-001", "sess-abc", "user-001")

    assert lock is not None
    assert lock.holder_user_id == "user-001"
    lock_repo.release.assert_called_once_with(SESSION_KEY)
    lock_repo.acquire.assert_called_once_with(SESSION_KEY, "user-001")


# --- get_session_lock_info --------------------------------------------------

def test_get_session_lock_info_not_locked(service, lock_repo):
    """无锁 -> locked=False，不查协作者。"""
    lock_repo.get_by_key.return_value = None

    result = service.get_session_lock_info("bot-123", "owner-001", "sess-abc", "user-001")

    assert result.locked is False
    assert result.lock is None
    assert result.holder_name is None
    assert result.is_mine is False


def test_get_session_lock_info_is_mine(service, lock_repo, collab_service):
    """自己持有 -> is_mine=True。不依赖"有无协作者"短路，直接按 key 查锁。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-001")
    collab = Mock()
    collab.user_id = "user-001"
    collab.user_name = "我自己"
    collab_service.list_collaborators.return_value = [collab]

    result = service.get_session_lock_info("bot-123", "owner-001", "sess-abc", "user-001")

    assert result.locked is True
    assert result.is_mine is True
    assert result.holder_name == "我自己"


def test_get_session_lock_info_held_by_other_resolves_name(service, lock_repo, collab_service):
    """他人持有 -> is_mine=False，且能从协作者列表解析持有者花名。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("user-002")
    collab = Mock()
    collab.user_id = "user-002"
    collab.user_name = "张三"
    collab_service.list_collaborators.return_value = [collab]

    result = service.get_session_lock_info("bot-123", "owner-001", "sess-abc", "user-001")

    assert result.locked is True
    assert result.is_mine is False
    assert result.holder_name == "张三"


def test_get_session_lock_info_holder_is_owner(service, lock_repo, collab_service, bot_service):
    """持有者是 owner -> 从 bot 信息取 owner_name。"""
    lock_repo.get_by_key.return_value = _make_session_lock_record("owner-001")
    collab_service.list_collaborators.return_value = []
    bot_service.get_bot.return_value = {"owner_name": "老板"}

    result = service.get_session_lock_info("bot-123", "owner-001", "sess-abc", "user-001")

    assert result.locked is True
    assert result.holder_name == "老板"
    assert result.is_mine is False


# --- 真 in-mem SQLite e2e（会话锁互斥语义）---------------------------------

def test_session_lock_reentrant_steal_release_on_real_sqlite(real_lock_service):
    """真库跑通：重入 / 他人挡 / 抢锁 / 释放后可再抢。"""
    svc = real_lock_service
    # 首次获取
    assert svc.acquire_session_lock("botX", "ownerX", "s1", "userA") is not None
    # 同用户重入
    assert svc.acquire_session_lock("botX", "ownerX", "s1", "userA") is not None
    # 他人被互斥挡掉
    assert svc.acquire_session_lock("botX", "ownerX", "s1", "userB") is None
    # 不同 session 互不影响
    assert svc.acquire_session_lock("botX", "ownerX", "s2", "userB") is not None
    # userB 抢 s1
    stolen = svc.steal_session_lock("botX", "ownerX", "s1", "userB")
    assert stolen.holder_user_id == "userB"
    # 原持有者 userA 已无法重入（已被抢）
    assert svc.acquire_session_lock("botX", "ownerX", "s1", "userA") is None
    # 持有者释放后可再抢
    assert svc.release_session_lock("botX", "ownerX", "s1", "userB") is True
    assert svc.acquire_session_lock("botX", "ownerX", "s1", "userA") is not None
