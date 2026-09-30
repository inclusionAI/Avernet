from __future__ import annotations

import os
from pathlib import Path
import json
from typing import Any, Iterable


BCN_BOT_UUID_ENV_KEY = "BCN_BOT_UUID"
SESSION_KEY_ENV_KEYS = (
    "HITL_SESSION_KEY",
    "ARCA_SESSION_KEY",
    "OPENCLAW_SESSION_KEY",
    "SESSION_KEY",
)
BOT_DATA_DIR_ENV_KEY = "BOT_DATA_DIR"

BOT_ID_ENV_KEYS = (
    "OPENCLAW_BOT_ID",
    "TEAMCLAW_BOT_ID",
    "CLAW_BOT_ID",
    "BOT_ID",
    "CURRENT_BOT_ID",
    "OPENCLAW_CURRENT_BOT_ID",
)
USER_ID_ENV_KEYS = (
    "CLAWBENCH_OWNER_ID",
    "OWNER_ID",
    "CLAWWEB_USER_ID",
    "OPENCLAW_USER_ID",
    "OPENCLAW_CURRENT_USER_ID",
    "TEAMCLAW_USER_ID",
    "CURRENT_USER_ID",
    "OUT_USER_NO",
    "USER_ID",
    "SENDER_ID",
    "DINGTALK_USER_ID",
)
COOKIE_ENV_KEYS = ("CLAWWEB_COOKIE", "OPENCLAW_COOKIE", "TEAMCLAW_COOKIE")


def _path_expand(value):
    return Path(value).expanduser()


def _read_json_safe(path):
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


CREDENTIAL_USER_ID_KEYS = ("OWNER_ID", "owner_id", "CLAWWEB_USER_ID", "user_id")
BOT_ID_KEYS = (
    "bot_id",
    "botId",
    "botID",
    "bot",
    "currentBotId",
    "current_bot_id",
    "teamclawBotId",
    "openclawBotId",
)
USER_ID_KEYS = (
    "user_id",
    "userId",
    "outUserNo",
    "out_user_no",
    "currentUserId",
    "current_user_id",
    "sender_id",
    "senderId",
    "operatorId",
    "operator_id",
)


def _clean_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "undefined"}:
        return ""
    return text[:120]


def _read_key_value_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = _clean_identifier(value)
    except Exception:
        return {}
    return result


def _user_id_from_credentials() -> tuple[str, str]:
    path = Path.home() / ".credentials"
    data = _read_key_value_file(path)
    for key in CREDENTIAL_USER_ID_KEYS:
        value = _clean_identifier(data.get(key, ""))
        if value:
            return value, f"{path}:{key}"
    return "", ""


def _first_env(keys: Iterable[str]) -> tuple[str, str]:
    for key in keys:
        value = _clean_identifier(os.environ.get(key, ""))
        if value:
            return value, f"env:{key}"
    return "", ""


def _walk_values(obj: Any, keys: tuple[str, ...]) -> str:
    if isinstance(obj, dict):
        for key in keys:
            value = _clean_identifier(obj.get(key))
            if value:
                return value
        for value in obj.values():
            found = _walk_values(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk_values(value, keys)
            if found:
                return found
    return ""


def _candidate_workspace_state_files(
    layout: dict[str, Any] | None = None,
) -> list[Path]:
    layout = layout or {}
    candidates: list[Path] = []
    for root in [Path.cwd(), _path_expand("~/.openclaw/workspace")]:
        candidates.extend(
            [root / ".openclaw" / "workspace-state.json", root / "workspace-state.json"]
        )
    for root in layout.get("agents", []) or []:
        p = Path(root)
        candidates.extend(
            [
                p / "workspace" / ".openclaw" / "workspace-state.json",
                p / "workspace" / "workspace-state.json",
                p / ".openclaw" / "workspace-state.json",
            ]
        )
    for root in layout.get("self_skill", []) or []:
        p = Path(root).resolve()
        for parent in [p, *list(p.parents)[:8]]:
            candidates.append(parent / ".openclaw" / "workspace-state.json")
            candidates.append(parent / "workspace-state.json")
    seen: set[str] = set()
    existing: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen and path.exists():
            existing.append(path)
            seen.add(key)
    return existing


def _candidate_bcs_session_files() -> list[Path]:
    candidates = []
    candidates.append(_path_expand("~/.openclaw/.bcs/session.json"))

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path.expanduser())
        if key not in seen:
            unique.append(path.expanduser())
            seen.add(key)
    return unique


def _value_from_json_files(
    files: Iterable[Path], keys: tuple[str, ...]
) -> tuple[str, str]:
    for path in files:
        value = _walk_values(_read_json_safe(path), keys)
        if value:
            return value, str(path)
    return "", ""


def _split_bot_uuid(value: str, source: str) -> tuple[str, str, str]:
    if not value or ":" not in value:
        return "", "", ""
    bot_id, user_id = value.split(":", 1)
    bot_id = _clean_identifier(bot_id)
    user_id = _clean_identifier(user_id)
    if not bot_id:
        return "", "", ""
    return bot_id, user_id, source


def _identity_from_bcn_bot_uuid() -> tuple[str, str, str]:
    return "", "", ""


def _identity_from_bcs_session_json() -> tuple[str, str, str]:
    for path in _candidate_bcs_session_files():
        data = _read_json_safe(path)
        if isinstance(data, dict):
            bot_id, user_id, source = _split_bot_uuid(
                str(data.get("bot_uuid", "")), str(path)
            )
            if bot_id:
                return bot_id, user_id, source
    return "", "", ""


def _user_id_from_session_key() -> tuple[str, str]:
    return "", ""


def _agent_id_from_session_key() -> tuple[str, str]:
    return "", ""


def resolve_runtime_bot_id(
    explicit_bot_id: str, rows: list[Any], layout: dict[str, Any]
) -> tuple[str, dict[str, str]]:
    """Resolve the current bot identity from runtime-provided OpenClaw/BCS signals."""
    if explicit_bot_id:
        return explicit_bot_id, {"source": "argument"}

    for resolver in (
        _identity_from_bcn_bot_uuid,
        _identity_from_bcs_session_json,
    ):
        bot_id, _, source = resolver()
        if bot_id:
            return bot_id, {"source": source}

    for resolver in (
        lambda: _first_env(BOT_ID_ENV_KEYS),
        lambda: _value_from_json_files(
            _candidate_workspace_state_files(layout), BOT_ID_KEYS
        ),
        lambda: _value_from_json_files(
            [layout.get("config")] if layout.get("config") else [], BOT_ID_KEYS
        ),
    ):
        value, source = resolver()
        if value:
            return value, {"source": source}

    bot_ids = sorted({str(r.bot_id) for r in rows if getattr(r, "bot_id", "")})
    if len(bot_ids) == 1:
        return bot_ids[0], {"source": "session_rows"}

    agent_id, source = _agent_id_from_session_key()
    if agent_id:
        return agent_id, {"source": source}

    agent_id = _clean_identifier(layout.get("agent_id"))
    if agent_id:
        return f"agent_{agent_id}", {"source": "agent_id_fallback"}
    return "current-bot", {"source": "fallback"}


def resolve_runtime_user_id(
    explicit_user_id: str = "", layout: dict[str, Any] | None = None
) -> tuple[str, dict[str, str]]:
    """Resolve the single ClawWeb x-user-id used for all domain/template APIs.

    Keep this intentionally simple and ClawWeb-compatible: prefer the explicit
    runtime environment user id, then ~/.credentials, then older OpenClaw
    session-derived fallbacks.
    """
    if explicit_user_id:
        return explicit_user_id, {"source": "argument"}

    value, source = _first_env(USER_ID_ENV_KEYS)
    if value:
        return value, {"source": source}

    value, source = _user_id_from_credentials()
    if value:
        return value, {"source": source}

    value, source = _user_id_from_session_key()
    if value:
        return value, {"source": source}

    _, value, source = _identity_from_bcn_bot_uuid()
    if value:
        return value, {"source": source}

    _, value, source = _identity_from_bcs_session_json()
    if value:
        return value, {"source": source}

    if layout:
        for resolver in (
            lambda: _value_from_json_files(
                [layout.get("config")] if layout.get("config") else [], USER_ID_KEYS
            ),
            lambda: _value_from_json_files(
                _candidate_workspace_state_files(layout), USER_ID_KEYS
            ),
        ):
            value, source = resolver()
            if value:
                return value, {"source": source}
    return "", {"source": "not_found"}


def resolve_runtime_cookie(explicit_cookie: str = "") -> tuple[str, dict[str, str]]:
    if explicit_cookie:
        return explicit_cookie, {"source": "argument"}
    value, source = _first_env(COOKIE_ENV_KEYS)
    if value:
        return value, {"source": source}
    return "", {"source": "not_found"}
