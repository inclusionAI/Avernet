"""本地持久化：token 文件、局内历史与 JSONL 事件日志。

token 只写入 0600 权限的用户目录；日志不记录 token，也不默认记录词语原文。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from pathlib import Path
from threading import Lock
from time import time


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
    """每个 session 只保留最近若干条助手输出，供 chat.history 查询。"""

    def __init__(self, limit: int = 200) -> None:
        self._sessions: dict[str, deque[dict[str, object]]] = {}
        self._session_ids: dict[str, str] = {}
        self._limit = limit

    def append(self, session_key: str, content: str, *, session_id: str | None = None) -> None:
        messages = self._sessions.setdefault(session_key, deque(maxlen=self._limit))
        messages.append({"role": "assistant", "content": content, "timestamp": int(time() * 1000)})
        if session_id is not None:
            self._session_ids[session_key] = session_id

    def messages(self, session_key: str) -> list[dict[str, object]]:
        return list(self._sessions.get(session_key, ()))

    def session_id(self, session_key: str) -> str | None:
        return self._session_ids.get(session_key)


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
