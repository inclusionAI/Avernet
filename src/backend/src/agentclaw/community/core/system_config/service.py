"""系统配置服务层

提供配置管理业务逻辑：

两表设计：
- ac_config_category: 分类目录表
- ac_config_item: 配置项表

配置项通过 parent_id 关联分类目录，环境隔离在分类目录层面。
"""

import json
from typing import Any

from injector import inject

from agentclaw.community.core.repository.protocols.config import ConfigRepositoryProtocol
from agentclaw.community.log import get_logger

logger = get_logger()


class SystemConfigService:
    """系统配置服务

    提供通用配置管理功能：
    - 分类目录管理
    - 配置项读写

    两表设计：
    - 分类目录表：存储分类元数据，包含环境隔离
    - 配置项表：存储具体配置，通过 parent_id 关联分类
    """

    @inject
    def __init__(self, repository: ConfigRepositoryProtocol) -> None:
        self._repo = repository

    def _parse_value(self, value: str | None) -> Any:
        """解析配置值"""
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def _serialize_value(self, value: Any) -> str:
        """序列化配置值"""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    # ============================================================
    # 分类目录管理
    # ============================================================

    def list_categories(self, *, env: str) -> list[dict[str, Any]]:
        """列出所有分类目录

        Args:
            env: 环境标识

        Returns:
            分类列表
        """
        categories = self._repo.list_categories(env=env)
        return [
            {
                "id": cat.id,
                "category": cat.category,
                "category_name": cat.category_name,
                "description": cat.description,
                "env": cat.env,
                "creator": cat.creator,
                "modifier": cat.modifier,
                "gmt_create": cat.gmt_create.isoformat() if cat.gmt_create else None,
                "gmt_modified": cat.gmt_modified.isoformat() if cat.gmt_modified else None,
            }
            for cat in categories
        ]

    def get_category_by_id(self, *, category_id: int) -> dict[str, Any] | None:
        """根据 ID 获取分类目录"""
        cat = self._repo.get_category_by_id(category_id=category_id)
        if cat is None:
            return None
        return {
            "id": cat.id,
            "category": cat.category,
            "category_name": cat.category_name,
            "description": cat.description,
            "env": cat.env,
        }

    def create_category(
        self,
        *,
        category: str,
        category_name: str,
        description: str | None = None,
        env: str,
        operator: str | None = None,
    ) -> int:
        """创建分类目录

        Args:
            category: 分类标识
            category_name: 分类名称（为空则使用默认映射）
            description: 描述
            env: 环境标识
            operator: 操作人

        Returns:
            分类 ID
        """
        category_id = self._repo.upsert_category(
            category=category,
            category_name=category_name,
            description=description,
            env=env,
            operator=operator,
        )
        logger.info(f"[create_category] Created: category={category}, env={env}, id={category_id}")
        return category_id

    def delete_category(self, *, category_id: int) -> bool:
        """删除分类目录（级联删除配置项）"""
        return self._repo.delete_category(category_id=category_id)

    # ============================================================
    # 配置读写
    # ============================================================

    def get_config(
        self, *, category: str, config_key: str, env: str
    ) -> Any:
        """获取配置值

        Args:
            category: 分类标识
            config_key: 配置键
            env: 环境标识

        Returns:
            配置值（已解析），不存在返回 None
        """
        # 先获取分类
        cat = self._repo.get_category(category=category, env=env)
        if cat is None:
            return None

        # 获取配置项
        config = self._repo.get_config_by_key(parent_id=cat.id, config_key=config_key)
        if config is None:
            return None

        return self._parse_value(config.config_value)

    def set_config(
        self,
        *,
        category: str,
        config_key: str,
        config_value: Any,
        env: str,
        description: str | None = None,
        operator: str | None = None,
    ) -> int:
        """设置配置值

        如果分类不存在会自动创建。

        Args:
            category: 分类标识
            config_key: 配置键
            config_value: 配置值
            env: 环境标识
            description: 配置描述
            operator: 操作人

        Returns:
            配置项 ID
        """
        # 确保分类存在
        cat = self._repo.get_category(category=category, env=env)
        if cat is None:
            logger.info(f"category not found!{category}")
            return 0
        else:
            parent_id = cat.id

        value_str = self._serialize_value(config_value)

        config_id = self._repo.upsert_config(
            parent_id=parent_id,
            config_key=config_key,
            config_value=value_str,
            description=description,
            operator=operator,
        )
        logger.info(f"[set_config] Set: category={category}, key={config_key}, env={env}")
        return config_id

    def delete_config(self, *, config_id: int) -> bool:
        """删除配置"""
        return self._repo.delete_config(config_id=config_id)

    def delete_config_by_key(self, *, category: str, config_key: str, env: str) -> bool:
        """根据键删除配置"""
        cat = self._repo.get_category(category=category, env=env)
        if cat is None:
            return False

        config = self._repo.get_config_by_key(parent_id=cat.id, config_key=config_key)
        if config is None:
            return False

        return self._repo.delete_config(config_id=config.id)

    def list_configs(self, *, category: str, env: str) -> list[dict[str, Any]]:
        """列出分类下的配置项

        Args:
            category: 分类标识
            env: 环境标识

        Returns:
            配置项列表
        """
        cat = self._repo.get_category(category=category, env=env)
        if cat is None:
            return []

        configs = self._repo.list_configs(parent_id=cat.id)
        return [
            {
                "id": c.id,
                "parent_id": c.parent_id,
                "config_key": c.config_key,
                "config_value": self._parse_value(c.config_value),
                "description": c.description,
                "creator": c.creator,
                "modifier": c.modifier,
                "gmt_create": c.gmt_create.isoformat() if c.gmt_create else None,
                "gmt_modified": c.gmt_modified.isoformat() if c.gmt_modified else None,
            }
            for c in configs
        ]

    def list_all_configs(self, *, env: str) -> list[dict[str, Any]]:
        """列出所有配置项（包含分类信息）

        Args:
            env: 环境标识

        Returns:
            配置项列表
        """
        return self._repo.list_all_configs(env=env)
