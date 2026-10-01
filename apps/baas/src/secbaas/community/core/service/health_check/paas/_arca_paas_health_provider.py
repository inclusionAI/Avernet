"""Arca 平台健康检查实现。"""

import asyncio
import json
import time

from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.logger import get_logger

from ._paas_health_provider import PaaSHealthProvider

logger = get_logger("core-service")


class EngineHealthChecker:
    """Engine 进程健康检查器（OpenClaw引擎）

    端点: 127.0.0.1:20003/api/engine/status
    健康判断: response.process.running == true
    """

    @property
    def name(self) -> str:
        return "engine"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd="curl -s http://127.0.0.1:20003/api/engine/status",
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            healthy = response.get("process", {}).get("running") is True
            return HealthCheckerStrategyResult(
                healthy=healthy,
                response=response,
                error=None if healthy else f"Engine not running: {response}",
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[EngineHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class AdapterHealthChecker:
    """Adapter 服务健康检查器

    端点: 127.0.0.1:20003/health
    健康判断: response.status == "ok"
    """

    @property
    def name(self) -> str:
        return "adapter"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd="curl -s http://127.0.0.1:20003/health",
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            healthy = response.get("status") == "ok"
            return HealthCheckerStrategyResult(
                healthy=healthy,
                response=response,
                error=None
                if healthy
                else f"Adapter unhealthy: status={response.get('status')}",
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[AdapterHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class GatewayHealthChecker:
    """Gateway 服务健康检查器

    端点: 127.0.0.1:18789/health
    健康判断: response.ok == true 或 response.status == "live"
    """

    @property
    def name(self) -> str:
        return "gateway"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd="curl -s http://127.0.0.1:18789/health",
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            ok = response.get("ok") is True
            status = response.get("status")
            healthy = ok or status == "live"
            return HealthCheckerStrategyResult(
                healthy=healthy,
                response=response,
                error=None
                if healthy
                else f"Gateway unhealthy: ok={ok}, status={status}",
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[GatewayHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class ActiveHealthChecker:
    """Bot 活跃状态检查器

    检查沙箱内是否有活跃的 session 文件或启用的 cron 任务，
    判断 Bot 是否处于活跃使用状态。
    """

    def __init__(self, minutes: int = 1440):
        self._minutes = minutes

    @property
    def name(self) -> str:
        return "active"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        cmd = (
            'DIR="/home/admin/.openclaw/agents/main/sessions"; '
            'CRON="/home/admin/.openclaw/cron/jobs.json"; '
            f"MIN={self._minutes}; "
            # 获取最近文件修改时间
            'LATEST=$(find "$DIR" -type f -printf "%T@\\n" 2>/dev/null | sort -rn | head -1); '
            'if [ -n "$LATEST" ]; then LAST_TIME=$(date -d "@${LATEST%.*}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null); else LAST_TIME=""; fi; '
            # 检查是否有启用的 cron 任务
            'if [ -f "$CRON" ] && grep -qE \'"enabled"\\s*:\\s*true\' "$CRON"; then HAS_CRON=true; else HAS_CRON=false; fi; '
            # 输出 JSON（使用 printf 避免引号问题）
            'printf "{\\"ok\\": true, \\"lastSessionTime\\": \\"%s\\", \\"hasEnabledCron\\": %s}\\n" "$LAST_TIME" "$HAS_CRON"'
        )
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            return HealthCheckerStrategyResult(
                healthy=True,
                response=response,
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[ActiveHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class HermesActiveHealthChecker:
    """Hermes 引擎活跃状态检查器

    检查 /home/admin/.hermes/sessions/ 下 session_*.json 文件的修改时间，
    判断 Hermes Bot 是否处于活跃使用状态。
    """

    def __init__(self, minutes: int = 1440):
        self._minutes = minutes

    @property
    def name(self) -> str:
        return "active_hermes"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        cmd = (
            'DIR="/home/admin/.hermes/sessions"; '
            f"MIN={self._minutes}; "
            'LATEST=$(find "$DIR" -type f -name "session_*.json" -printf "%T@\\n" 2>/dev/null | sort -rn | head -1); '
            'if [ -n "$LATEST" ]; then LAST_TIME=$(date -d "@${LATEST%.*}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null); else LAST_TIME=""; fi; '
            'printf "{\\"ok\\": true, \\"lastSessionTime\\": \\"%s\\", \\"hasEnabledCron\\": false}\\n" "$LAST_TIME"'
        )
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            return HealthCheckerStrategyResult(
                healthy=True,
                response=response,
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[HermesActiveHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class ClaudeCodeActiveHealthChecker:
    """Claude Code / AICoding 引擎活跃状态检查器

    检查 /home/admin/.claude/projects/ 下 .jsonl 文件的修改时间，
    判断 Claude Code / AICoding Bot 是否处于活跃使用状态。
    """

    def __init__(self, minutes: int = 1440):
        self._minutes = minutes

    @property
    def name(self) -> str:
        return "active_claude_code"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        cmd = (
            'DIR="/home/admin/.claude/projects"; '
            f"MIN={self._minutes}; "
            'LATEST=$(find "$DIR" -type f -name "*.jsonl" -printf "%T@\\n" 2>/dev/null | sort -rn | head -1); '
            'if [ -n "$LATEST" ]; then LAST_TIME=$(date -d "@${LATEST%.*}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null); else LAST_TIME=""; fi; '
            'printf "{\\"ok\\": true, \\"lastSessionTime\\": \\"%s\\", \\"hasEnabledCron\\": false}\\n" "$LAST_TIME"'
        )
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            return HealthCheckerStrategyResult(
                healthy=True,
                response=response,
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[ClaudeCodeActiveHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class EchoHealthChecker:
    """Echo 探活检查器

    向沙箱发送 echo 命令，通过命令执行成功与否判断沙箱是否存活。
    支持可选的 engine_name 参数，用于按引擎类型区分 echo payload。

    - 无 engine_name: echo '{"code": 0, "status": "alive"}'
    - 有 engine_name: echo '{"code": 0, "engine": "<engine_name>"}'
    """

    def __init__(self, engine_name: str | None = None):
        self._engine_name = engine_name

    @property
    def name(self) -> str:
        if self._engine_name:
            return f"echo_{self._engine_name}"
        return "echo"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        if self._engine_name:
            payload = json.dumps({"code": 0, "engine": self._engine_name})
        else:
            payload = json.dumps({"code": 0, "status": "alive"})
        cmd = f"echo '{payload}'"
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Command failed with exit code {result.exit_code}: {result.stderr}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            return HealthCheckerStrategyResult(
                healthy=True,
                response=response,
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[EchoHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class ArcaPaaSHealthProvider(PaaSHealthProvider):
    """Arca 平台健康检查实现。

    支持 engine, adapter, gateway, active 四个组件的检查。
    """

    def __init__(self, paas_facade: "PaasServiceFacade", timeout_seconds: int = 10):
        self._paas_facade = paas_facade
        self._timeout_seconds = timeout_seconds
        self._all_checkers = {
            "engine": EngineHealthChecker(),
            "adapter": AdapterHealthChecker(),
            "gateway": GatewayHealthChecker(),
            "active": ActiveHealthChecker(),
            "active_hermes": HermesActiveHealthChecker(),
            "active_claude_code": ClaudeCodeActiveHealthChecker(),
            "echo": EchoHealthChecker(),
            "echo_aicoding": EchoHealthChecker(engine_name="aicoding"),
            "echo_claude_code": EchoHealthChecker(engine_name="claude_code"),
        }

    async def check_health(
        self,
        paas_device_id: str,
        checkers: list[str],
    ) -> PaasHealthCheckerResult:
        """执行健康检查。

        Args:
            paas_device_id: PaaS 设备 ID
            checkers: 需要执行的检查器列表

        Returns:
            PaasHealthCheckerResult 包含各检查器的结果
        """
        logger.info(
            f"[ArcaPaaSHealthProvider] check_health for {paas_device_id}, checkers={checkers}"
        )

        # 选择需要执行的检查器
        selected_checkers = {
            name: checker
            for name, checker in self._all_checkers.items()
            if name in checkers
        }

        if not selected_checkers:
            logger.warning(f"[ArcaPaaSHealthProvider] No valid checkers for {checkers}")
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=True,  # 无检查器时默认健康
                checkers={},
            )

        # 并发执行检查
        async def run_checker(
            name: str,
            checker: EngineHealthChecker
            | AdapterHealthChecker
            | GatewayHealthChecker
            | ActiveHealthChecker,
        ) -> tuple[str, HealthCheckerStrategyResult]:
            try:
                result = await asyncio.wait_for(
                    checker.check(paas_device_id, self._paas_facade),
                    timeout=self._timeout_seconds,
                )
                return name, result
            except TimeoutError:
                return name, HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Timeout after {self._timeout_seconds}s",
                    timeout=True,
                    duration_ms=int(self._timeout_seconds * 1000),
                )
            except Exception as e:
                logger.error(f"[ArcaPaaSHealthProvider] checker {name} failed: {e}")
                return name, HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=str(e),
                    timeout=False,
                    duration_ms=0,
                )

        tasks = [
            run_checker(name, checker) for name, checker in selected_checkers.items()
        ]
        results_list = await asyncio.gather(*tasks)
        results = dict(results_list)

        overall_healthy = all(r.healthy for r in results.values())

        return PaasHealthCheckerResult(
            paas_device_id=paas_device_id,
            overall_healthy=overall_healthy,
            checkers=results,
        )

    async def check_alive(
        self,
        paas_device_id: str,
        minutes: int = 1440,
        checkers: list[str] | None = None,
    ) -> HealthCheckerStrategyResult:
        """检查设备是否活跃（alive）。

        Args:
            paas_device_id: PaaS 设备 ID
            minutes: 检查最近 N 分钟内是否有活跃会话
            checkers: 需要执行的检查器列表，必须由策略解析提供

        Returns:
            HealthCheckerStrategyResult 包含活跃检查结果

        Raises:
            ValueError: checkers 为空或未提供时
        """
        if not checkers:
            raise ValueError(
                f"check_alive requires checkers to be specified, "
                f"got checkers={checkers} for device {paas_device_id}. "
                f"Use resolve_alive_check_strategy to determine checkers."
            )

        # 使用策略指定的检查器
        selected = {
            name: checker
            for name, checker in self._all_checkers.items()
            if name in checkers
        }
        if not selected:
            raise ValueError(
                f"No valid alive checkers found for {checkers}, "
                f"available: {list(self._all_checkers.keys())}"
            )

        # 对 alive 检查，取第一个匹配的 checker 执行
        for name, checker in selected.items():
            # ActiveHealthChecker 需要 minutes 参数，重新创建实例
            if name == "active":
                checker = ActiveHealthChecker(minutes=minutes)
            elif name == "active_hermes":
                checker = HermesActiveHealthChecker(minutes=minutes)
            elif name == "active_claude_code":
                checker = ClaudeCodeActiveHealthChecker(minutes=minutes)
            return await checker.check(paas_device_id, self._paas_facade)

        # 不应到达此处
        raise ValueError(f"No alive checker executed for {checkers}")
