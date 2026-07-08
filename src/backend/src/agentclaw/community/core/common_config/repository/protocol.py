"""通用配置仓库协议定义。"""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.common_config.models import CommonConfigRecord


@runtime_checkable
class CommonConfigRepositoryProtocol(Protocol):
    """``ac_common_config`` 仓库协议。"""

    def get_by_id(self, *, config_id: int) -> CommonConfigRecord | None: ...

    def get_by_biz_param(
        self, *, business_code: str, param_code: str, env: str
    ) -> CommonConfigRecord | None: ...

    def list_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[CommonConfigRecord]: ...

    def count_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
    ) -> int: ...

    def create_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: str | None,
        business_name: str | None,
        param_code: str,
        enable: str,
        ext_info: str | None,
        env: str,
    ) -> int: ...

    def update_config(self, *, config_id: int, updates: dict) -> bool: ...

    def upsert_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: str | None,
        business_name: str | None,
        param_code: str,
        enable: str,
        ext_info: str | None,
        env: str,
    ) -> int: ...

    def delete_config(self, *, config_id: int) -> bool: ...

    def delete_by_biz_param(
        self, *, business_code: str, param_code: str, env: str
    ) -> bool: ...
