import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from engine.community.config import MCPTokenSettings, load_mcp_token_settings

log = logging.getLogger("mcp_token")

# session_key 格式: session:{uuid}:user:{user_id}
SESSION_KEY_USER_PATTERN = re.compile(r":user:([^:]+)$")


def extract_user_id_from_session_key(session_key: str) -> Optional[str]:
    """
    从 session_key 中解析 user_id

    格式: session:{uuid}:user:{user_id}
    示例: session:test-session-id:user:test-user

    Returns:
        user_id 或 None（如果格式不匹配）
    """
    if not session_key:
        return None
    match = SESSION_KEY_USER_PATTERN.search(session_key)
    if match:
        return match.group(1)
    return None


def extract_mcp_token(params: Dict[str, Any], header_name: str) -> Optional[str]:
    # 统一优先从配置 header 读取，兼容回退到 auth.token
    token = params.get(header_name)
    if not token:
        auth = params.get("auth")
        if isinstance(auth, dict):
            token = auth.get("token")
    if isinstance(token, str):
        normalized = token.strip()
        if normalized:
            return normalized
    return None


def build_upstream_headers(
    token: Optional[str],
    settings: Optional[MCPTokenSettings] = None,
) -> Dict[str, str]:
    # 开关关闭时不向上游透传 token，保证行为可配置
    if not token:
        return {}
    effective_settings = settings or load_mcp_token_settings()
    if not effective_settings.forward_to_wss:
        return {}
    return {effective_settings.header_name: token}


def persist_mcp_token(
    token: str,
    settings: Optional[MCPTokenSettings] = None,
) -> None:
    effective_settings = settings or load_mcp_token_settings()
    if not effective_settings.persist_enabled:
        log.debug("[persist_mcp_token] persist disabled, skipping")
        return
    store_path = effective_settings.store_path
    # 启动时/首次写入自动创建目录，避免路径不存在导致失败
    store_path.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())

    existing_data: Dict[str, Any] = {}
    if store_path.exists():
        try:
            with open(store_path, "r", encoding="utf-8") as f:
                parsed = json.load(f)
            if isinstance(parsed, dict):
                existing_data = parsed
        except (OSError, json.JSONDecodeError):
            # 文件损坏时自动自愈为新结构，不阻塞主链路
            existing_data = {}

    tokens = existing_data.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}

    current_entry = tokens.get(effective_settings.header_name)
    if not isinstance(current_entry, dict):
        current_entry = {}

    current_entry["token"] = token
    current_entry["updated_at_epoch_secs"] = now
    current_entry["last_used_at_epoch_secs"] = now
    tokens[effective_settings.header_name] = current_entry

    output = {
        "version": 1,
        "tokens": tokens,
    }
    _atomic_write_json(store_path, output)


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    # 临时文件 + 原子替换，避免写入中断造成半文件
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp_file:
        tmp_file.write(json.dumps(data, ensure_ascii=False, indent=2))
        tmp_file.write("\n")
        temp_path = Path(tmp_file.name)
    os.replace(temp_path, path)
