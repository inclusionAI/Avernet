"""
引擎类型常量。

从 services/openclawserver/server/config.py 迁移。
新架构中统一从此处 import，不再引用旧 config。
"""
import os

# 支持的引擎类型列表
SUPPORTED_ENGINE_TYPES = ["moltis", "openclaw", "hermes", "aicoding", "claude_code"]

# Backend 的默认引擎类型，硬编码为 openclaw。
# 每个 bot 的实际引擎类型由 bot 自身属性决定，此值仅作 fallback。
DEFAULT_ENGINE_TYPE = "openclaw"


def _get_engine_types() -> list[str]:
    """从环境变量或默认列表获取引擎类型。

    优先级:
    1. ENGINE_TYPES 环境变量（逗号分隔）
    2. SUPPORTED_ENGINE_TYPES 默认列表

    Returns:
        list[str]: 引擎类型列表，如 ["moltis", "openclaw", "hermes", "aicoding"]
    """
    env_engines = os.getenv("ENGINE_TYPES", "")
    if env_engines:
        return [e.strip() for e in env_engines.split(",") if e.strip()]
    return SUPPORTED_ENGINE_TYPES
