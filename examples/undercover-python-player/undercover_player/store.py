"""本地持久化：token 文件、局内历史与 JSONL 事件日志。

token 只写入 0600 权限的用户目录；日志不记录 token，也不默认记录词语原文。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Lock
from time import monotonic, time


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, str] | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(value, dict):
            return None
        token = value.get("token")
        bot_uuid = value.get("bot_uuid")
        if not isinstance(token, str) or not token or not isinstance(bot_uuid, str) or not bot_uuid:
            return None
        return {key: str(value.get(key, "")) for key in ("bcs_url", "bot_uuid", "token", "bot_name")}

    def save(self, *, bcs_url: str, bot_uuid: str, token: str, bot_name: str) -> None:
        _private_dir(self._path.parent)
        payload = {"bcs_url": bcs_url, "bot_uuid": bot_uuid, "token": token, "bot_name": bot_name}
        descriptor, temporary = tempfile.mkstemp(dir=self._path.parent, prefix=".session-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return


class HistoryStore:
    """每个 session 的 chat.history 记录；限制会话数、单会话条数和空闲 TTL。

    ``active_session_keys`` 由 BcnClient 传入活跃 run 的 session；清理时绝不删除
    这些 session。活跃 session 会刷新 last_used，避免长 run 刚结束就被 TTL 清掉。
    """

    def __init__(
        self,
        limit: int = 200,
        *,
        max_sessions: int = 64,
        ttl_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if limit < 1 or max_sessions < 1 or ttl_seconds <= 0:
            raise ValueError("HistoryStore limits must be positive")
        self._sessions: OrderedDict[str, deque[dict[str, object]]] = OrderedDict()
        self._session_ids: dict[str, str] = {}
        self._last_used: dict[str, float] = {}
        self._limit = limit
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def append(
        self,
        session_key: str,
        content: str,
        *,
        session_id: str | None = None,
        active_session_keys: Iterable[str] = (),
    ) -> None:
        messages = self._sessions.get(session_key)
        if messages is None:
            messages = deque(maxlen=self._limit)
            self._sessions[session_key] = messages
        messages.append({"role": "assistant", "content": content, "timestamp": int(time() * 1000)})
        if session_id is not None:
            self._session_ids[session_key] = session_id
        self._touch(session_key)
        self.prune(active_session_keys)

    def messages(
        self,
        session_key: str,
        *,
        active_session_keys: Iterable[str] = (),
    ) -> list[dict[str, object]]:
        keep = frozenset(active_session_keys) | {session_key}
        self.prune(keep)
        if session_key not in self._sessions:
            return []
        self._touch(session_key)
        return list(self._sessions[session_key])

    def session_id(self, session_key: str) -> str | None:
        return self._session_ids.get(session_key)

    def prune(self, active_session_keys: Iterable[str] = ()) -> None:
        active = frozenset(active_session_keys)
        now = self._clock()
        for key in list(self._sessions):
            if key in active:
                self._last_used[key] = now
                continue
            if now - self._last_used.get(key, now) > self._ttl_seconds:
                self._remove(key)
        while len(self._sessions) > self._max_sessions:
            victim = next((key for key in self._sessions if key not in active), None)
            if victim is None:
                break
            self._remove(victim)

    def _touch(self, session_key: str) -> None:
        self._last_used[session_key] = self._clock()
        if session_key in self._sessions:
            self._sessions.move_to_end(session_key)

    def _remove(self, session_key: str) -> None:
        self._sessions.pop(session_key, None)
        self._session_ids.pop(session_key, None)
        self._last_used.pop(session_key, None)


class SessionMemory:
    """session -> (seat, word) 的短期记忆；限制会话数、空闲 TTL，保护活跃 run。"""

    def __init__(
        self,
        *,
        max_sessions: int = 128,
        ttl_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_sessions < 1 or ttl_seconds <= 0:
            raise ValueError("SessionMemory limits must be positive")
        self._entries: OrderedDict[str, tuple[int | None, str | None]] = OrderedDict()
        self._last_used: dict[str, float] = {}
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def get(
        self,
        session_id: str,
        *,
        active_session_keys: Iterable[str] = (),
    ) -> tuple[int | None, str | None] | None:
        self.prune(active_session_keys)
        if session_id not in self._entries:
            return None
        self._touch(session_id)
        return self._entries[session_id]

    def remember(
        self,
        session_id: str,
        seat: int | None,
        word: str | None,
        *,
        active_session_keys: Iterable[str] = (),
    ) -> None:
        self.prune(active_session_keys)
        self._entries[session_id] = (seat, word)
        self._entries.move_to_end(session_id)
        self._touch(session_id)
        self.prune(active_session_keys)

    def prune(self, active_session_keys: Iterable[str] = ()) -> None:
        active = frozenset(active_session_keys)
        now = self._clock()
        for key in list(self._entries):
            if key in active:
                self._last_used[key] = now
                continue
            if now - self._last_used.get(key, now) > self._ttl_seconds:
                self._entries.pop(key, None)
                self._last_used.pop(key, None)
        while len(self._entries) > self._max_sessions:
            victim = next((key for key in self._entries if key not in active), None)
            if victim is None:
                break
            self._entries.pop(victim, None)
            self._last_used.pop(victim, None)

    def __len__(self) -> int:
        return len(self._entries)

    def _touch(self, session_id: str) -> None:
        self._last_used[session_id] = self._clock()
        if session_id in self._entries:
            self._entries.move_to_end(session_id)


class EventLog:
    """单文件 JSONL 日志；写入失败不改变业务结果。"""

    def __init__(self, directory: Path, *, session_tag: str | None = None) -> None:
        _private_dir(directory)
        stamp = session_tag or str(int(time() * 1000))
        self._path = directory / f"{stamp}.jsonl"
        self._lock = Lock()
        self._path.touch(mode=0o600, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: str, **fields: object) -> None:
        record = {"ts": round(time() * 1000), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError:
                return
