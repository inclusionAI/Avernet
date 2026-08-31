"""
引擎类型常量。

从 services/openclawserver/server/config.py 迁移。
新架构中统一从此处 import，不再引用旧 config。
"""
import os

# 支持的引擎类型列表
SUPPORTED_ENGINE_TYPES = [
    "moltis",
    "openclaw",
    "hermes",
    "aicoding",
    "claude_code",
    "teclaw",
]

# Backend 的默认引擎类型，硬编码为 openclaw。
# 每个 bot 的实际引擎类型由 bot 自身属性决定，此值仅作 fallback。
DEFAULT_ENGINE_TYPE = "openclaw"

# 内部实现类引擎：不是可对外创建的产品引擎，只是某个产品引擎的运行时实现
# （如 aicoding 是 claude_code 的内部实现）。形态信息记录在 template 的
# 拓展字段（runtime_identity.ENGINE_FORM_KEY），不再作为 engine 值创建新 bot。
# 存量 bot 的读路径（engine_types 列、{engine}_conf 目录、引擎过滤、
# bucket 解析）仍需识别它们，故 SUPPORTED_ENGINE_TYPES 保持全集不动。
INTERNAL_ENGINE_TYPES = frozenset({"aicoding"})


def _get_engine_types() -> list[str]:
    """从环境变量或默认列表获取引擎类型。

    优先级:
    1. ENGINE_TYPES 环境变量（逗号分隔）
    2. SUPPORTED_ENGINE_TYPES 默认列表

    Returns:
        list[str]: 引擎类型列表，如 ["moltis", "openclaw", "hermes", "aicoding", "teclaw"]
    """
    env_engines = os.getenv("ENGINE_TYPES", "")
    if env_engines:
        return [e.strip() for e in env_engines.split(",") if e.strip()]
    return SUPPORTED_ENGINE_TYPES
