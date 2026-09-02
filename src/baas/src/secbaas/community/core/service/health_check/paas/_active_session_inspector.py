"""Active Session Inspector — 通过 engine `/api/engine/active-sessions` 查询设备活跃会话状态。

本模块为 BaaS 侧 device drain 提供"是否还有活跃 session"的事实源，替代原来以
`bot_session` 仓储 `count_active_sessions_by_device` 为唯一事实源、并在异常时降级
为 0 放行 drain 的旧实现。

契约（engine `GET /api/engine/active-sessions` 公布的 dual-axis 字段）：

- engine 响应 `ActiveSessionQueryResponse` 同时给出 `query_status`（transport 侧的
  查询状态）和 `verdict`（业务侧的活跃判定）。
- BaaS 侧 `ActiveSessionVerdict` 收敛为三态：`CLEAR` / `ACTIVE` / `UNKNOWN`。
- 任意失败（curl `exit_code != 0`、`json.JSONDecodeError`、字段缺失/类型不兼容、
  `TimeoutError`、未捕获 `Exception`、响应不完整）一律收敛 `UNKNOWN`，**不降级
  `CLEAR`**，以避免误下线。

行为映射表（封闭规则）：

| engine `query_status` | engine `verdict` | BaaS `ActiveSessionVerdict` |
|---|---|---|
| `ok`                       | `clear`                  | `CLEAR`   |
| `ok`                       | `active`                 | `ACTIVE`  |
| `ok`                       | `unknown` 或缺 `verdict` | `UNKNOWN` |
| `unsupported`              | *                        | `UNKNOWN`（HTTP 200 亦归 unknown）|
| `timeout`                  | *                        | `UNKNOWN` |
| `error` 或未知/缺字段       | *                        | `UNKNOWN` |

落点与同包的 `_arca_paas_health_provider.py` 中的 `EngineHealthChecker` /
`AdapterHealthChecker` 等保持一致：通过 `PaasServiceFacade.execute_command`
发送 `curl`，统一 `duration_ms`、`TimeoutError` / `Exception` 分支与返回结构。

不持有 mutable state；构造仅注入必要常量（端点 URL、默认超时）。bot/device 隔离由
调用方（如 `_publish_service._get_active_sessions`）解析 `paas_device_id` 后传入，
Inspector 自身不做跨 bot/device 聚合，亦不写库。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.service.paas import PaasServiceFacade

logger = get_logger("core-service")

# 与 EngineHealthChecker 同端口的本地 in-sandbox engine active-sessions 端点。
# 127.0.0.1:20003 为沙箱内本地端口，可入日志；不外抛内部 URL/hostname。
DEFAULT_ACTIVE_SESSIONS_ENDPOINT = "http://127.0.0.1:20003/api/engine/active-sessions"
DEFAULT_ACTIVE_SESSIONS_TIMEOUT_SECONDS = 10


class ActiveSessionVerdict(StrEnum):
    """Drain 决策用的活跃会话判定三态。

    - `CLEAR`：engine 明确返回无活跃 session，drain 可推进。
    - `ACTIVE`：engine 明确返回仍有活跃 session，drain 应等待。
    - `UNKNOWN`：查询失败、契约不支持或字段缺失；drain **不应放行**，由调用方按
      超时分支处理，避免误下线。
    """

    CLEAR = "clear"
    ACTIVE = "active"
    UNKNOWN = "unknown"


@dataclass
class ActiveSessionInspectResult:
    """`ActiveSessionInspector.inspect` 的返回结构。

    Attributes:
        verdict: 收敛后的三态判定，drain 决策的唯一事实源。
        query_status: engine 返回的 `query_status` 原值（审计/可观测）。
        raw_response: engine 响应解析后的 dict（失败时为 None）。
        duration_ms: 端到端查询耗时（含 curl + execute_command）。
        error: 失败原因（脱敏后的客户端安全文案）。
        timeout: 是否因 `TimeoutError` 收敛为 UNKNOWN。
    """

    verdict: ActiveSessionVerdict
    query_status: str | None = None
    raw_response: Any = None
    duration_ms: int = 0
    error: str | None = None
    timeout: bool = False


class ActiveSessionInspector:
    """查询沙箱内 engine `/api/engine/active-sessions` 并按 dual-axis 契约给 verdict。

    Inspector 仅负责按 device 发起 engine 查询与契约映射；不做跨 bot/device 聚合，
    也不写库。所有失败一律收敛 `UNKNOWN`，调用方据此阻塞 drain。
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_ACTIVE_SESSIONS_ENDPOINT,
        timeout_seconds: int = DEFAULT_ACTIVE_SESSIONS_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds

    async def inspect(
        self,
        paas_device_id: str,
        paas_facade: PaasServiceFacade,
        bot_id: int | str | None = None,
        device_id: int | str | None = None,
        lifecycle_stage: str = "online",
        timeout_seconds: int | None = None,
    ) -> ActiveSessionInspectResult:
        """对单个 device 发起 active-sessions 查询并返回收敛后的 verdict。

        Args:
            paas_device_id: 形如 ``{statefulset}--{ordinal}@{template_id}`` 的 PaaS
                设备 ID，由调用方经 ``_device_repo``/绑定解析得到。
            paas_facade: 用于在沙箱内执行 ``curl`` 的 PaaS 服务门面。
            bot_id: 审计/日志字段，标识所属 bot（不参与查询）。
            device_id: 审计/日志字段，标识业务 device（不参与查询）。
            lifecycle_stage: draft/verify/online 等生命周期阶段（仅审计用）。
            timeout_seconds: 单次查询超时；为 ``None`` 时使用构造默认值。

        Returns:
            ``ActiveSessionInspectResult``；任意失败路径均返回 ``verdict=UNKNOWN``。
        """
        effective_timeout = (
            timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        )
        audit_ctx = (
            f"bot_id={bot_id}, device_id={device_id}, "
            f"paas_device_id={paas_device_id}, lifecycle_stage={lifecycle_stage}"
        )
        cmd = f"curl -s {self._endpoint}"
        start_time = time.time()

        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=effective_timeout,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                logger.warning(
                    f"[ActiveSessionInspector] curl failed: {audit_ctx}, "
                    f"exit_code={result.exit_code}, duration_ms={duration_ms}"
                )
                return ActiveSessionInspectResult(
                    verdict=ActiveSessionVerdict.UNKNOWN,
                    duration_ms=duration_ms,
                    error=f"curl exit_code={result.exit_code}",
                )

            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"[ActiveSessionInspector] json decode failed: {audit_ctx}, "
                    f"duration_ms={duration_ms}"
                )
                return ActiveSessionInspectResult(
                    verdict=ActiveSessionVerdict.UNKNOWN,
                    duration_ms=duration_ms,
                    error="response is not valid JSON",
                )

            if not isinstance(payload, dict):
                logger.warning(
                    f"[ActiveSessionInspector] response not a JSON object: "
                    f"{audit_ctx}, duration_ms={duration_ms}"
                )
                return ActiveSessionInspectResult(
                    verdict=ActiveSessionVerdict.UNKNOWN,
                    duration_ms=duration_ms,
                    error="response is not a JSON object",
                )

            query_status = payload.get("query_status")
            verdict_field = payload.get("verdict")
            mapped = self._map_verdict(query_status, verdict_field)

            logger.info(
                f"[ActiveSessionInspector] ok: {audit_ctx}, "
                f"query_status={query_status}, verdict={mapped.value}, "
                f"duration_ms={duration_ms}"
            )
            return ActiveSessionInspectResult(
                verdict=mapped,
                query_status=query_status if isinstance(query_status, str) else None,
                raw_response=payload,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning(
                f"[ActiveSessionInspector] timeout: {audit_ctx}, "
                f"duration_ms={duration_ms}"
            )
            return ActiveSessionInspectResult(
                verdict=ActiveSessionVerdict.UNKNOWN,
                duration_ms=duration_ms,
                error=f"Timeout after {effective_timeout}s",
                timeout=True,
            )
        except Exception as e:  # noqa: BLE001 — inspector 收敛所有异常为 UNKNOWN
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning(
                f"[ActiveSessionInspector] unexpected error: {audit_ctx}, "
                f"duration_ms={duration_ms}, error={e}"
            )
            return ActiveSessionInspectResult(
                verdict=ActiveSessionVerdict.UNKNOWN,
                duration_ms=duration_ms,
                error=str(e),
            )

    @staticmethod
    def _map_verdict(
        query_status: Any, verdict_field: Any
    ) -> ActiveSessionVerdict:
        """按 §3.2 行为映射表将 engine dual-axis 字段收敛为 BaaS 三态。"""
        if query_status == "ok":
            if verdict_field == "clear":
                return ActiveSessionVerdict.CLEAR
            if verdict_field == "active":
                return ActiveSessionVerdict.ACTIVE
            # verdict ∈ {unknown, 缺失, 其他} -> UNKNOWN
            return ActiveSessionVerdict.UNKNOWN
        # query_status ∈ {unsupported, timeout, error, 缺失, 未知} -> UNKNOWN
        # （HTTP 200 + unsupported 时 engine 仍按 200 返回，BaaS 侧归 unknown）
        return ActiveSessionVerdict.UNKNOWN