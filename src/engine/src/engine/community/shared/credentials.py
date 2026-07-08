"""
Credentials Service

读取 ~/.credentials 文件，提供 TOKEN、CLIENT_ID、OWNER_ID、BOT_ID、AGENT_CODE 等字段的访问。

文件格式示例：
    TOKEN=xxxxxxxx
    CLIENT_ID=staff_xxxxxx
    OWNER_ID=xxxxxx
    BOT_ID=xxxxx_xxxx
    AGENT_CODE=agent_xxxxx
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("credentials")


def _get_env_case_insensitive(key: str) -> Optional[str]:
    """Look up an environment variable by key, ignoring case."""
    key_lower = key.lower()
    for k, v in os.environ.items():
        if k.lower() == key_lower:
            return v
    return None


# 默认路径
DEFAULT_CREDENTIALS_PATH = "/home/admin/.credentials"


@dataclass
class Credentials:
    """凭证数据"""
    token: Optional[str] = None
    client_id: Optional[str] = None
    owner_id: Optional[str] = None
    bot_id: Optional[str] = None
    agent_code: Optional[str] = None
    role: Optional[str] = None
    visibility: Optional[str] = None
    admins: Optional[str] = None


class CredentialsService:
    """
    凭证服务 - 读取和管理 ~/.credentials 文件

    使用方式：
        service = CredentialsService.get_instance()
        token = service.get_token()
        owner_id = service.get_owner_id()
    """

    _instance: Optional[CredentialsService] = None
    _credentials: Optional[Credentials] = None
    _path: Optional[Path] = None

    def __init__(self, path: Optional[Path] = None):
        self._path = path
        self._credentials = None

    @classmethod
    def get_instance(cls) -> CredentialsService:
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（用于测试）"""
        cls._instance = None

    def _get_path(self) -> Path:
        """获取凭证文件路径"""
        if self._path:
            return self._path
        raw = os.getenv("CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)
        return Path(raw).expanduser().resolve()

    def _load(self) -> Credentials:
        """加载凭证文件"""
        path = self._get_path()
        creds = Credentials()

        if not path.exists():
            log.warning(f"[credentials] 文件不存在: {path}")
            return creds

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue

                    key, _, value = line.partition("=")
                    key = key.strip().upper()
                    value = value.strip()

                    if key == "TOKEN":
                        creds.token = value
                    elif key == "CLIENT_ID":
                        creds.client_id = value
                    elif key == "OWNER_ID":
                        creds.owner_id = value
                    elif key == "BOT_ID":
                        creds.bot_id = value
                    elif key == "AGENT_CODE":
                        creds.agent_code = value
                    elif key == "ROLE":
                        creds.role = value
                    elif key == "VISIBILITY":
                        creds.visibility = value
                    elif key == "ADMINS":
                        creds.admins = value

            log.info(f"[credentials] 加载成功: path={path}")
        except Exception as e:
            log.error(f"[credentials] 加载失败: {e}")

        return creds

    def _ensure_loaded(self) -> Credentials:
        """确保凭证已加载"""
        if self._credentials is None:
            self._credentials = self._load()
        return self._credentials

    def reload(self) -> None:
        """重新加载凭证文件"""
        self._credentials = None
        self._ensure_loaded()

    def get_token(self) -> Optional[str]:
        """获取 TOKEN"""
        return self._ensure_loaded().token

    def get_client_id(self) -> Optional[str]:
        """获取 CLIENT_ID"""
        return self._ensure_loaded().client_id

    def get_owner_id(self) -> Optional[str]:
        """获取 OWNER_ID"""
        return self._ensure_loaded().owner_id

    def get_bot_id(self) -> Optional[str]:
        """获取 BOT_ID"""
        return self._ensure_loaded().bot_id

    def get_agent_code(self) -> Optional[str]:
        """获取 AGENT_CODE，优先从 credentials 文件读取，fallback 到环境变量（忽略大小写）"""
        agent_code = self._ensure_loaded().agent_code
        if agent_code:
            return agent_code
        return _get_env_case_insensitive("AGENT_CODE")

    def get_all(self) -> Credentials:
        """获取所有凭证数据"""
        return self._ensure_loaded()

    def is_loaded(self) -> bool:
        """检查凭证是否已加载"""
        return self._credentials is not None

    def get_role(self) -> Optional[str]:
        """获取 ROLE"""
        return self._ensure_loaded().role

    def get_visibility(self) -> Optional[str]:
        """获取 VISIBILITY"""
        return self._ensure_loaded().visibility

    def get_admins(self) -> Optional[str]:
        """获取 ADMINS（逗号分隔的管理员工号）"""
        return self._ensure_loaded().admins

    def update_fields(self, updates: Dict[str, str]) -> None:
        """
        更新凭证文件中的字段

        Args:
            updates: 要更新的字段字典，如 {"ROLE": "OWNER", "VISIBILITY": "PUBLIC"}
        """
        path = self._get_path()

        # 复制字典，避免修改调用者的数据
        remaining = dict(updates)

        # 读取现有内容
        lines = []

        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if "=" in stripped and not stripped.startswith("#"):
                        key = stripped.partition("=")[0].strip().upper()
                        if key in remaining:
                            # 替换已有字段
                            lines.append(f"{key}={remaining.pop(key)}\n")
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)

        # 追加新字段
        for key, value in remaining.items():
            lines.append(f"{key}={value}\n")

        # 写回文件
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        # 重新加载缓存
        self.reload()


# 便捷函数
def get_credentials_service() -> CredentialsService:
    """获取 CredentialsService 单例"""
    return CredentialsService.get_instance()


def get_token() -> Optional[str]:
    """获取 TOKEN"""
    return get_credentials_service().get_token()


def get_client_id() -> Optional[str]:
    """获取 CLIENT_ID"""
    return get_credentials_service().get_client_id()


def get_owner_id() -> Optional[str]:
    """获取 OWNER_ID"""
    return get_credentials_service().get_owner_id()


def get_bot_id() -> Optional[str]:
    """获取 BOT_ID"""
    return get_credentials_service().get_bot_id()


def get_agent_code() -> Optional[str]:
    """获取 AGENT_CODE，优先从 credentials 文件读取，fallback 到环境变量（忽略大小写）"""
    return get_credentials_service().get_agent_code()


def get_role() -> Optional[str]:
    """获取 ROLE"""
    return get_credentials_service().get_role()


def get_visibility() -> Optional[str]:
    """获取 VISIBILITY"""
    return get_credentials_service().get_visibility()


def get_admins() -> Optional[str]:
    """获取 ADMINS（逗号分隔的管理员工号）"""
    return get_credentials_service().get_admins()
