"""Task-mode roster cache and list transport for BcnService.

Extracted to keep the facade within the file-size cap. Shared transport
errors live in bcn_transport_types so this mixin never imports the facade.
"""

import time
from typing import Any, Dict, List, Optional

import httpx

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.bot_management.services.bcn_transport_types import (
    BcnRequestError,
    BcnServiceError,
    _get_provider_config,
    bcn_http_error,
)

logger = get_logger()


class BcnRosterMixin:
    """``list_bots_by_task_modes`` + its process-local TTL cache.

    Composed onto :class:`BcnService`; the cache state lives on the service
    instance (``_roster_cache`` / ``_roster_cache_lock``, initialized in the
    facade ``__init__``).
    """

    # list_bots_by_task_modes 命中 BCS 出网查询的进程内 TTL 缓存(roster 静态、变更罕见)。
    # 仅缓存成功结果;异常不缓存(交调用方 fail-open)。BcnService 为 DI 单例,缓存随实例存活。
    # 跨 worker 不共享(各进程各一份),roster 短窗口内可接受 ≤TTL 的旧值。参考 MarketCache 内存层。
    TASK_MODE_ROSTER_CACHE_TTL: float = 60.0

    def _roster_cache_get(self, key: tuple) -> Optional[List[Dict[str, Any]]]:
        """读进程内 TTL 缓存;未命中/过期返回 None(后续走真实 HTTP,不缓存错误)。"""
        now = time.time()
        with self._roster_cache_lock:
            entry = self._roster_cache.get(key)
            if entry is None:
                return None
            items, ts = entry
            if now - ts > self.TASK_MODE_ROSTER_CACHE_TTL:
                return None
            return list(items)  # 浅拷贝,上层 append/pop 不污染缓存

    def _roster_cache_set(self, key: tuple, items: List[Dict[str, Any]]) -> None:
        """写进程内 TTL 缓存(仅成功结果);存浅拷贝以防外部引用被改。"""
        with self._roster_cache_lock:
            self._roster_cache[key] = (list(items), time.time())

    def invalidate_task_mode_roster_cache(self) -> None:
        """清空 task-mode roster 进程内缓存。dream_mode 开关变更后可调用以立即生效。"""
        with self._roster_cache_lock:
            self._roster_cache.clear()

    def list_bots_by_task_modes(
        self,
        *,
        claim: bool | None = None,
        dream: bool | None = None,
        match: str = "any",
        visibility: str | None = None,
        status: str | None = None,
        user_visibility: str | None = None,
    ) -> List[Dict[str, Any]]:
        """查询满足任务模式开关的 provider bot roster(BCS provider 路由,Bearer)。

        ``GET /providers/{provider_id}/bots/by-task-modes`` ——与 register/switch/attributes
        provider-bot 同套鉴权:仅 ``Authorization: Bearer {provider_admin_token}``，``provider_id``
        在 path，复用 ``_get_provider_config`` 解析的统一 provider 身份(BcnConfig)。与其它
        BcnService 方法一致直接返回 BCN 响应原结构(``{"items": [...]}`` 取 ``items``)。

        ``claim``/``dream`` 为 ``None`` 表示该开关不过滤(不下发 query)；``match`` 为 any|all。
        ``visibility``、``status``、``user_visibility`` 为 ``None`` 表示不按对应
        返回字段过滤；provider 身份直接读 ``BcnConfig``(部署 overlay 已给定)。
        非 prod/pre 或凭据空时抛 :class:`BcnServiceError`(BBS 调用方按 fail-open 处理)。
        """
        env = get_current_env()
        visibility = visibility.strip() if visibility is not None else None
        status = status.strip() if status is not None else None
        user_visibility = (
            user_visibility.strip() if user_visibility is not None else None
        )
        # 进程内 TTL 缓存命中优先(同 env/任务模式/元数据过滤条件的重复请求
        # 收敛为秒级命中);
        # 命中外网成功后再回填,异常不缓存。
        cache_key = (env, claim, dream, match, visibility, status, user_visibility)
        cached = self._roster_cache_get(cache_key)
        if cached is not None:
            logger.info(
                "[BcnService.list_bots_by_task_modes] HIT env=%s claim=%s dream=%s match=%s",
                env,
                claim,
                dream,
                match,
            )
            return cached
        provider_cfg = _get_provider_config(env, self._config)
        if not provider_cfg:
            raise BcnServiceError(
                f"task-mode roster provider credentials not configured for env={env}"
            )
        provider_id = provider_cfg["provider_id"]
        token = provider_cfg["provider_admin_token"]
        path = f"/providers/{provider_id}/bots/by-task-modes"
        params: Dict[str, str] = {"match": match}
        if claim is not None:
            params["task_claim_mode"] = "true" if claim else "false"
        if dream is not None:
            params["task_dream_mode"] = "true" if dream else "false"
        for name, value in ((
            ("visibility", visibility),
            ("status", status),
            ("user_visibility", user_visibility),
        )):
            if value:
                params[name] = value
        logger.info(
            "[BcnService.list_bots_by_task_modes] GET %s claim=%s dream=%s match=%s "
            "visibility=%s status=%s user_visibility=%s",
            path,
            claim,
            dream,
            match,
            visibility,
            status,
            user_visibility,
        )
        try:
            response = self._http.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            items = items if isinstance(items, list) else []
            self._roster_cache_set(cache_key, items)
            return items
        except httpx.HTTPStatusError as e:
            raise bcn_http_error(
                "BCN list_bots_by_task_modes HTTP error",
                e.response.status_code if e.response else None,
            )
        except httpx.TimeoutException as e:
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "timeout",
                f"BCN list_bots_by_task_modes timeout: {e}",
            )
        except BcnServiceError:
            raise
        except Exception as e:  # pragma: no cover - 传输层安全网
            raise BcnRequestError(
                DeliveryFailure.UNKNOWN,
                "unknown",
                f"BCN list_bots_by_task_modes error: {e}",
            )
