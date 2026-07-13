"""
Schema Loader

加载和管理 JSON Schema，提供校验功能。

M0 实现：基础的 Schema 加载和校验。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import RefResolver


class SchemaLoader:
    """
    JSON Schema 加载器

    加载指定目录下的所有 JSON Schema，并构建 $ref 解析器。
    """

    def __init__(self, schema_dir: Path | str):
        """
        初始化 Schema 加载器

        Args:
            schema_dir: Schema 文件所在目录
        """
        self.schema_dir = Path(schema_dir)
        self._store: dict[str, dict] = {}
        self._load_schemas()

    def _load_schemas(self) -> None:
        """加载所有 Schema 文件"""
        if not self.schema_dir.exists():
            raise FileNotFoundError(f"Schema directory not found: {self.schema_dir}")

        for path in self.schema_dir.glob("*.json"):
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
                # 以文件名作为 key
                self._store[path.name] = schema
                # 如果有 $id，也以 $id 作为 key
                schema_id = schema.get("$id")
                if schema_id:
                    self._store[schema_id] = schema
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in schema file {path}: {e}")

    @property
    def store(self) -> dict[str, dict]:
        """返回 Schema store"""
        return self._store

    def get_schema(self, name: str) -> dict:
        """
        获取指定名称的 Schema

        Args:
            name: Schema 文件名或 $id

        Returns:
            Schema dict

        Raises:
            KeyError: Schema 不存在
        """
        if name not in self._store:
            raise KeyError(f"Schema not found: {name}")
        return self._store[name]

    def validate(self, instance: dict, schema_name: str) -> None:
        """
        校验实例是否符合指定 Schema

        Args:
            instance: 待校验的数据
            schema_name: Schema 文件名或 $id

        Raises:
            jsonschema.ValidationError: 校验失败
        """
        schema = self.get_schema(schema_name)
        resolver = RefResolver(
            base_uri=schema.get("$id", f"file://{(self.schema_dir / schema_name).as_posix()}"),
            referrer=schema,
            store=self._store,
        )
        jsonschema.validate(instance, schema, resolver=resolver)


def validate_with_store(
    instance: dict[str, Any],
    schema_name: str,
    store: dict[str, dict] | None = None,
) -> None:
    """
    使用 Schema store 校验实例

    这是便捷函数，用于在已有 store 的情况下进行校验。

    Args:
        instance: 待校验的数据
        schema_name: Schema 文件名或 $id
        store: Schema store（可选，如果不提供会自动加载）

    Raises:
        jsonschema.ValidationError: 校验失败
    """
    if store is None:
        # 默认使用项目根目录下的 schemas/
        schema_dir = Path(__file__).resolve().parents[2] / "schemas"
        loader = SchemaLoader(schema_dir)
        store = loader.store
        schema = store[schema_name]
        resolver = RefResolver(
            base_uri=schema.get("$id", f"file://{(schema_dir / schema_name).as_posix()}"),
            referrer=schema,
            store=store,
        )
    else:
        schema = store[schema_name]
        schema_dir = Path(__file__).resolve().parents[2] / "schemas"
        resolver = RefResolver(
            base_uri=schema.get("$id", f"file://{(schema_dir / schema_name).as_posix()}"),
            referrer=schema,
            store=store,
        )

    jsonschema.validate(instance, schema, resolver=resolver)


__all__ = [
    "SchemaLoader",
    "validate_with_store",
]