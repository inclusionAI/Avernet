"""
引擎进程管理抽象

定义引擎进程的生命周期接口，各引擎实现自己的启停逻辑。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from asyncio.subprocess import DEVNULL
import httpx

from engine.community.config import EngineProcessSettings, load_engine_process_settings

log = logging.getLogger("engine-process")


class EngineProcess(ABC):
    """引擎进程抽象接口"""

    @abstractmethod
    async def start(self) -> None:
        """启动引擎进程"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止引擎进程"""
        ...

    @abstractmethod
    async def restart(self) -> None:
        """重启引擎进程"""
        ...

    @abstractmethod
    async def is_running(self) -> bool:
        """检查引擎进程是否运行中"""
        ...

    def status(self) -> dict:
        """返回进程状态快照"""
        return {"running": False}


class CommandEngineProcess(EngineProcess):
    """基于命令的引擎进程实现"""

    def __init__(self, settings: EngineProcessSettings):
        self._settings = settings
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    async def start(self) -> None:
        async with self._lock:
            if not self._settings.enabled:
                log.info(
                    "Engine process command disabled: engine=%s",
                    self._settings.engine,
                )
                return
            if self._has_healthcheck() and await self._check_health():
                log.info(
                    "Engine process already running externally: engine=%s healthcheck=%s",
                    self._settings.engine,
                    self._healthcheck_label(),
                )
                return
            if await self.is_running():
                return

            log.info("start engine, cmd=%s", self._settings.start_cmd)
            try:
                # 关键：通过子进程命令托管引擎，避免 adapter 强耦合具体引擎实现
                self._proc = await asyncio.create_subprocess_exec(
                    *self._settings.start_cmd,
                    cwd=str(self._settings.workdir) if self._settings.workdir else None,
                    stdout=DEVNULL,
                    stderr=DEVNULL,
                )
                await self._wait_ready()
                self._last_error = None
                log.info(
                    "Engine process started: engine=%s pid=%s cmd=%s",
                    self._settings.engine,
                    self._proc.pid if self._proc else None,
                    " ".join(self._settings.start_cmd),
                )
            except Exception as e:
                self._last_error = str(e)
                log.error(
                    "Engine process start failed: engine=%s err=%s",
                    self._settings.engine,
                    e,
                )
                raise

    async def stop(self) -> None:
        async with self._lock:
            proc = self._proc
            if proc is None:
                if self._settings.stop_cmd:
                    await self._run_stop_cmd()
                return
            if proc.returncode is not None:
                self._proc = None
                return

            try:
                if self._settings.stop_cmd:
                    await self._run_stop_cmd()

                if proc.returncode is None:
                    proc.terminate()
                    await asyncio.wait_for(
                        proc.wait(),
                        timeout=self._settings.graceful_timeout_sec,
                    )
            except TimeoutError:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            finally:
                self._proc = None
                log.info("Engine process stopped: engine=%s", self._settings.engine)

    async def restart(self) -> None:
        async with self._lock:
            if not self._settings.restart_cmd:
                raise ValueError(
                    f"restart_cmd not configured: engine={self._settings.engine}"
                )
            await self._run_restart_cmd()
            self._proc = None
            await self._wait_restart_ready()
            self._last_error = None
            log.info(
                "Engine process restarted: engine=%s cmd=%s",
                self._settings.engine,
                " ".join(self._settings.restart_cmd),
            )

    async def is_running(self) -> bool:
        proc = self._proc
        if proc is None:
            if self._has_healthcheck():
                return await self._check_health()
            return False
        if proc.returncode is not None:
            if self._has_healthcheck():
                return await self._check_health()
            return False
        if not self._has_healthcheck():
            return True
        return await self._check_health()

    def status(self) -> dict:
        proc = self._proc
        running = proc is not None and proc.returncode is None
        exited_code = None if proc is None else proc.returncode
        pid = proc.pid if running and proc is not None else None
        return {
            "running": running,
            "pid": pid,
            "exit_code": exited_code,
            "last_error": self._last_error,
            "command_enabled": self._settings.enabled,
            "managed_process": proc is not None,
        }

    async def _wait_ready(self) -> None:
        timeout = max(0.5, self._settings.startup_timeout_sec)
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if self._proc is None:
                raise RuntimeError("process handle lost")
            if self._proc.returncode is not None:
                if self._proc.returncode != 0:
                    raise RuntimeError(f"process exited early: code={self._proc.returncode}")
                if self._has_healthcheck():
                    if await self._check_health():
                        return
                else:
                    return

            if self._has_healthcheck():
                if await self._check_health():
                    return
            else:
                await asyncio.sleep(0.2)
                if self._proc.returncode is None:
                    return

            if asyncio.get_event_loop().time() >= deadline:
                raise TimeoutError(
                    f"process startup timeout: {self._settings.startup_timeout_sec}s"
                )
            await asyncio.sleep(0.2)

    async def _check_tcp_health(self, healthcheck_tcp: str) -> bool:
        try:
            host, port_raw = healthcheck_tcp.split(":", 1)
            port = int(port_raw)
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def _check_http_health(self, url: str) -> bool:
        timeout = 10.0
        started = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            if 200 <= resp.status_code < 300:
                return True
            snippet = resp.text[:200] if resp.text else ""
            log.warning(
                "Engine health probe non-2xx: url=%s status=%s body=%r",
                url, resp.status_code, snippet,
            )
            return False
        except httpx.TimeoutException:
            elapsed = asyncio.get_event_loop().time() - started
            log.warning(
                "Engine health probe timeout: url=%s after %.2fs (limit=%.1fs)",
                url, elapsed, timeout,
            )
            return False
        except httpx.ConnectError as e:
            log.warning("Engine health probe connect error: url=%s err=%r", url, e)
            return False
        except Exception as e:
            log.warning("Engine health probe error: url=%s err=%r", url, e)
            return False

    def _has_healthcheck(self) -> bool:
        return bool(self._settings.healthcheck_http or self._settings.healthcheck_tcp)

    async def _check_health(self) -> bool:
        # HTTP readiness probe is preferred — TCP only confirms the listener
        # is open, which can be true before the gateway has finished settling
        # sidecars/channels/hooks. Falls back to TCP when no HTTP URL set.
        if self._settings.healthcheck_http:
            return await self._check_http_health(self._settings.healthcheck_http)
        if self._settings.healthcheck_tcp:
            return await self._check_tcp_health(self._settings.healthcheck_tcp)
        return False

    def _healthcheck_label(self) -> str:
        return self._settings.healthcheck_http or self._settings.healthcheck_tcp or ""

    async def _run_stop_cmd(self) -> None:
        stop_proc = await asyncio.create_subprocess_exec(
            *self._settings.stop_cmd,
            cwd=str(self._settings.workdir) if self._settings.workdir else None,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        await asyncio.wait_for(
            stop_proc.wait(),
            timeout=self._settings.graceful_timeout_sec,
        )
        if stop_proc.returncode != 0:
            raise RuntimeError(
                f"stop_cmd failed: engine={self._settings.engine} code={stop_proc.returncode}"
            )

    async def _run_restart_cmd(self) -> None:
        restart_proc = await asyncio.create_subprocess_exec(
            *self._settings.restart_cmd,
            cwd=str(self._settings.workdir) if self._settings.workdir else None,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        await asyncio.wait_for(
            restart_proc.wait(),
            timeout=self._settings.graceful_timeout_sec,
        )
        if restart_proc.returncode != 0:
            raise RuntimeError(
                f"restart_cmd failed: engine={self._settings.engine} code={restart_proc.returncode}"
            )

    async def _wait_restart_ready(self) -> None:
        if not self._has_healthcheck():
            return
        timeout = max(0.5, self._settings.startup_timeout_sec)
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if await self._check_health():
                return
            if asyncio.get_event_loop().time() >= deadline:
                raise TimeoutError(
                    f"process restart timeout: {self._settings.startup_timeout_sec}s"
                )
            await asyncio.sleep(0.2)


class NoOpEngineProcess(EngineProcess):
    """No-op process for engines the adaptor doesn't need to babysit.

    Used when the engine has no adaptor-wide singleton subprocess — either
    because the engine has no subprocess at all, or because each session
    owns its own subprocess managed inside the plugin code (e.g. Claude
    Code's per-session CLI invocations live in the SessionService).

    All lifecycle methods succeed silently; `is_running()` returns True so
    the manager treats the engine as ready.
    """

    def __init__(self, engine: str = ""):
        self._engine = engine

    async def start(self) -> None:
        log.info("NoOpEngineProcess.start: engine=%s (no adaptor-managed subprocess)", self._engine)

    async def stop(self) -> None:
        log.info("NoOpEngineProcess.stop: engine=%s (no adaptor-managed subprocess)", self._engine)

    async def restart(self) -> None:
        log.info("NoOpEngineProcess.restart: engine=%s (no adaptor-managed subprocess)", self._engine)

    async def is_running(self) -> bool:
        return True

    def status(self) -> dict:
        return {"running": True, "managed_process": False, "engine": self._engine}


def create_engine_process(
    engine: str, settings: EngineProcessSettings | None = None
) -> EngineProcess:
    """Build the right `EngineProcess` for `engine` based on its settings.

    Dispatch order:

    - If ``process.enabled`` is True and ``start_cmd`` is non-empty, return a
      :class:`CommandEngineProcess` to manage the subprocess.
    - Otherwise return a :class:`NoOpEngineProcess` — the engine has no
      adapter-wide subprocess for the manager to babysit (per-session
      processes, if any, are owned by the engine's plugin code).

    No general engine-name allowlist: any name registered with
    EngineRegistry can be constructed. Whether subprocess management
    actually happens is determined by the rules above.

    ``settings`` may be supplied directly (the DI path — the caller resolves
    the per-engine ``EngineProcessSettings`` via the injected resolver). When
    omitted it falls back to the pure, stateless ``load_engine_process_settings``
    reader (engine.json + env; no module-global cache). Full caller-side
    threading of the resolver is F5.
    """
    normalized = engine.strip().lower()
    if settings is None:
        settings = load_engine_process_settings(normalized)
    if not settings.enabled:
        return NoOpEngineProcess(normalized)
    return CommandEngineProcess(settings)
