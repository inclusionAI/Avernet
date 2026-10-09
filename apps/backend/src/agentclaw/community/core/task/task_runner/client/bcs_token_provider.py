"""BCS HMAC 凭据(driver bot 签名取数)。具体实现由组合根配置注入。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BcsTokenProvider(Protocol):
    @property
    def token(self) -> str: ...

    @property
    def secret(self) -> str: ...

    @property
    def base_url(self) -> str: ...

    @property
    def task_callback_url(
        self,
    ) -> str: ...  # 任务回投 origin(scheme://netloc);corp env-aware 注入,空→TaskExecutor 兜底 api_base_url
