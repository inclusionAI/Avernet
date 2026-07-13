"""
数据路径配置

提供统一的数据路径解析，确保写入和查询使用相同的绝对路径。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_data_path(db_path: str) -> str:
    """
    将数据路径解析为绝对路径

    如果路径是相对路径，则基于项目根目录解析
    （项目根目录为 bcsfuse 目录）

    Args:
        db_path: 数据库路径（相对或绝对）

    Returns:
        str: 绝对路径
    """
    if os.path.isabs(db_path):
        return db_path

    # 获取当前文件路径，并向上查找项目根目录
    # 当前文件: src/infra/config/data_paths.py
    # 项目根目录: 包含 src 的目录 (bcsfuse)
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent  # 向上4级到 bcsfuse

    absolute_path = project_root / db_path
    resolved_path = str(absolute_path.resolve())

    logger.debug("[resolve_data_path] 相对路径 '%s' 解析为 '%s'", db_path, resolved_path)
    return resolved_path


def get_default_vector_store_path() -> str:
    """获取默认向量存储路径（绝对路径）"""
    return resolve_data_path("data/vector_store.db")
