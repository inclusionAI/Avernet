"""
EngineManager — the active Engine instance + its lifecycle.

Holds one `_active_engine: Engine` slot and exposes `.session` / `.chat` /
`.cron` / `.relay` as passthrough properties plus `on_connection_open` /
`on_connection_close` lifecycle hooks; the WS server and HTTP routers
dispatch through these. Engine construction goes through `EngineRegistry`
(DEFAULT_REGISTRY, aliased as `base_registry._REGISTRY`), so adding a new
engine is an import line in `engine.engines.__init__` away — the engine
package self-registers on import.

Static helpers `register()` / `create_engine()` / `current()` / `get_registered_engine_names()`
are thin wrappers on DEFAULT_REGISTRY for callers that want registry access
without having to import the registry module directly.

Responsibilities the manager still owns on top of the engine:
- Engine subprocess lifecycle
- WebSocket server singleton reset across switches (the server is generic,
  but its per-connection state cache must be dropped when the active engine
  changes)
- Cron polling — engine-agnostic worker that runs above the plugin boundary.
  Engine-specific cron workers (e.g. OpenClaw's SystemEvent replacement
  monitor) live inside the engine's own `initialize()` / `shutdown()`.
- Legacy model / node adapters (until M6 lifts them to plugins)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from engine.community.core.engine.registry import DEFAULT_REGISTRY, EngineRegistry

if TYPE_CHECKING:
    from injector import Injector

    from engine.community.core.approval.protocol import ApprovalService
    from engine.community.core.bash.protocol import BashService
    from engine.community.core.chat.protocol import ChatService
    from engine.community.core.cron.protocol import CronService
    from engine.community.core.engine.capability import EngineCapabilities
    from engine.community.core.engine.context import AuthContext
    from engine.community.core.engine.protocol import Engine
    from engine.community.core.default_config.protocol import DefaultConfigService
    from engine.community.core.file.protocol import FileService
    from engine.community.core.mcp.protocol import MCPService
    from engine.community.core.models.protocol import ModelsService
    from engine.community.core.node.protocol import NodeService
    from engine.community.plugin_api.notification.protocol import NotificationService
    from engine.community.core.relay.protocol import RelayService
    from engine.community.core.session.protocol import SessionService
    from engine.community.core.skills.protocol import SkillsService
    from engine.community.core.web_shell.protocol import WebShellService

log = logging.getLogger("engine-manager")


class EngineManager:
    """引擎运行时管理器（单例）"""

    _instance: EngineManager | None = None
    # Composition-root injector, bound once at app startup
    # (``api/app.py``) and unbound at shutdown. When set, ``get_instance()``
    # resolves the manager from it (the DI singleton) rather than constructing
    # one lazily. ``None`` outside a running app — non-DI unit tests fall back
    # to the legacy lazy path.
    _injector: Injector | None = None

    def __init__(
        self,
        engine: str,
        registry: EngineRegistry | None = None,
        notification_service: "NotificationService | None" = None,
    ) -> None:
        self._engine = engine
        self._registry = registry if registry is not None else DEFAULT_REGISTRY
        self._notification_service = notification_service
        self._lock = asyncio.Lock()
        self._active_engine: Engine | None = None
        self._process: Any = None  # EngineProcess
        self._cron_polling: Any = None  # CronPollingService
        self._transition: dict[str, Any] | None = None
        self._last_init_error: str | None = None
        # Lifecycle phase used by readiness():
        #   "starting"        — actively in initialize/switch/restart, before
        #                       the subprocess start step has been attempted
        #   "start_attempted" — we tried to start the subprocess and waited
        #                       for it (succeeded, timed out, or raised); the
        #                       enclosing init coroutine is still running
        #   "completed"       — initialize/switch/restart returned cleanly
        #   "failed"          — initialize/switch/restart raised
        self._init_phase: str = "starting"

    @classmethod
    def bind_injector(cls, injector: Injector | None) -> None:
        """Bind (or unbind) the composition-root injector.

        Called by the composition root (``api/app.py``) on app startup with the
        process injector, and with ``None`` on shutdown. When bound,
        :meth:`get_instance` resolves the manager from the injector singleton.
        """
        cls._injector = injector

    @classmethod
    def get_instance(cls) -> EngineManager:
        """获取单例实例.

        With the composition-root injector bound (running app), resolve the
        manager from it — the DI ``ManagerModule`` provides it as a singleton
        seeded with ``EngineConfig.default_engine``. Without an injector (non-DI
        unit tests), fall back to the legacy lazy construction.
        """
        if cls._injector is not None:
            return cls._injector.get(EngineManager)
        if cls._instance is None:
            # Import the engines package so every bundled engine registers
            # itself with DEFAULT_REGISTRY before construction.
            import engine.community.engines  # noqa: F401
            from engine.community.config import load_engine_config

            cls._instance = cls(load_engine_config().default_engine)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）.

        Clears both the legacy lazy singleton and any bound injector so a test
        that exercised the DI path doesn't leak the binding into the next test.
        """
        cls._instance = None
        cls._injector = None

    # ── 新式注册表接口（T0.2） ──────────────────────────────────────────
    # 这些方法把进程级的 EngineRegistry 搬到 manager 上，方便 web 层和
    # 测试用例不必单独 import base_registry 就能查到已注册的引擎类。
    # 它们都是薄包装，真正的状态存放在
    # :data:`engine.community.core.engine.registry.DEFAULT_REGISTRY` 的模块级单例里。

    @staticmethod
    def register(engine_class: "type[Engine]") -> None:
        """注册一个引擎类到进程级注册表（通常由引擎包在 import 时触发）。

        等价于 ``DEFAULT_REGISTRY.register(engine_class)``，但提供给 manager
        使用者时语义更清晰：注册不是 manager 的生命周期动作，而是直接
        写入进程级表。重复注册同一个类是幂等的；不同类同名会抛 ``EngineError``。
        """
        DEFAULT_REGISTRY.register(engine_class)

    @staticmethod
    def get_registered_engine_names() -> tuple[str, ...]:
        """Return the canonical names of every registered engine.

        Thin wrapper on ``DEFAULT_REGISTRY.names()``. Use this when you need
        a bare list of engine identifiers (e.g. alias-resolution tests);
        `/api/engine/list` uses the instance-level :meth:`get_registered_engines`
        which returns richer `{name, version, active}` entries.
        """
        return tuple(DEFAULT_REGISTRY.names())

    @staticmethod
    def create_engine(name: str, config: dict | None = None) -> "Engine":
        """Instantiate an engine by name.

        ``name`` is normalized by :func:`engine.community.core.engine.naming.normalize`
        inside the registry so ``"aicoding"`` / ``"claude_code"`` /
        ``"claude-code"`` all resolve to :class:`AiCodingEngine`. Unknown
        names raise :class:`EngineNotFoundError`.
        """
        engine_cls = DEFAULT_REGISTRY.get(name)
        return engine_cls(config or {})

    def current(self) -> str:
        """Return the active engine's canonical name.

        Unlike :attr:`engine`, this re-runs alias normalization so callers
        always see the canonical spelling (``"claude_code"`` → ``"aicoding"``
        etc.). Returns the raw stored name when the current value is not a
        known alias.
        """
        from engine.community.core.engine.naming import normalize

        return normalize(self._engine) or self._engine

    # ── Metadata / plugin passthroughs ─────────────────────────────────────

    @property
    def engine(self) -> str:
        """当前活跃引擎名称"""
        return self._engine

    # ── Introspection (§18.2) ──────────────────────────────────────────────

    def get_capabilities(self, engine: str | None = None) -> "EngineCapabilities":
        """Return the declared capabilities for an engine.

        `engine=None` returns the active engine's capabilities (after initialize()
        has run). Passing a name returns the declared capabilities of a registered
        engine without activating it — used by `/api/engine/capabilities?engine=X`
        so the frontend can preview what a non-active engine would support before
        switching.

        For the inspection path, the engine is instantiated with empty config so
        `cls.capabilities` can be evaluated. Engines whose constructor needs
        non-trivial config should declare a class-level `_CAPABILITIES` attribute
        (the convention used by OpenClawEngine) so this call doesn't need real
        wiring.
        """
        if engine is None or engine == self._engine:
            active = self._active_engine
            if active is not None:
                return active.capabilities
        target = engine if engine is not None else self._engine
        cls = self._registry.get(target)
        class_caps = getattr(cls, "_CAPABILITIES", None)
        from engine.community.core.engine.capability import EngineCapabilities as _EC

        if isinstance(class_caps, _EC):
            return class_caps
        instance = cls()
        return instance.capabilities

    def get_registered_engines(self) -> list[dict[str, Any]]:
        """List every engine in the registry with lightweight metadata.

        Returns a list of `{name, version, active}` entries for
        `/api/engine/list`. `active=True` marks the engine currently owning
        `_active_engine`. Version is read from the class attribute so the
        engine doesn't need to be instantiated.
        """
        out: list[dict[str, Any]] = []
        for name in self._registry.names():
            cls = self._registry.get(name)
            version = getattr(cls, "version", "unknown")
            out.append(
                {
                    "name": name,
                    "version": version if isinstance(version, str) else "unknown",
                    "active": name == self._engine,
                }
            )
        return out

    @property
    def session(self) -> SessionService:
        """Active engine's SessionService."""
        return self._require_engine().session

    @property
    def chat(self) -> ChatService:
        """Active engine's ChatService."""
        return self._require_engine().chat

    @property
    def cron(self) -> CronService:
        """Active engine's CronService.

        Raises CapabilityNotSupportedError when the engine does not provide
        cron — cron is optional on the Engine Protocol.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        cron = self._require_engine().cron
        if cron is None:
            raise CapabilityNotSupportedError(self._engine, Capability.CRON_LIST)
        return cron

    @property
    def relay(self) -> RelayService | None:
        """Active engine's RelayService, or None when the engine doesn't
        expose transparent passthrough. The generic WebSocket server calls
        this to route unknown methods / raw frames through the engine's
        upstream; a `None` return makes the server 501 on unknown methods.
        """
        return self._require_engine().relay

    @property
    def mcp(self) -> MCPService:
        """Active engine's MCPService.

        Raises CapabilityNotSupportedError when the engine doesn't expose
        MCP. The HTTP routes in `api/mcp/router.py` short-circuit before
        touching this via `check_capability(MCP_LIST/CREATE/...)`, so
        callers normally see a 501 from the guard rather than this error.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        mcp = self._require_engine().mcp
        if mcp is None:
            raise CapabilityNotSupportedError(self._engine, Capability.MCP_LIST)
        return mcp

    @property
    def skills(self) -> SkillsService:
        """Active engine's SkillsService.

        Raises CapabilityNotSupportedError when the engine doesn't expose
        skills. The HTTP routes in `api/skills/router.py` short-circuit
        before touching this via `check_capability(SKILLS_*)`.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        skills = self._require_engine().skills
        if skills is None:
            raise CapabilityNotSupportedError(
                self._engine, Capability.SKILLS_LIST,
            )
        return skills

    @property
    def web_shell(self) -> WebShellService:
        """Active engine's WebShellService.

        Raises CapabilityNotSupportedError when the engine has no debug
        terminal. The HTTP / WS routes in `api/web_shell/router.py`
        short-circuit via `check_capability(WEB_SHELL_OPEN)` first.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        ws = self._require_engine().web_shell
        if ws is None:
            raise CapabilityNotSupportedError(
                self._engine, Capability.WEB_SHELL_OPEN,
            )
        return ws

    @property
    def default_config(self) -> DefaultConfigService:
        """Active engine's DefaultConfigService.

        Raises CapabilityNotSupportedError when the engine has no
        default-config surface. The HTTP route in
        `api/default_config/router.py` short-circuits via
        `check_capability(DEFAULT_CONFIG_GET)` first.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        dc = self._require_engine().default_config
        if dc is None:
            raise CapabilityNotSupportedError(
                self._engine, Capability.DEFAULT_CONFIG_GET,
            )
        return dc

    @property
    def file(self) -> FileService:
        """Active engine's FileService.

        Raises CapabilityNotSupportedError when the engine has no
        workspace FS surface. The HTTP routes in `api/file/router.py`
        short-circuit via `check_capability(FILE_*)` before touching this.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        file = self._require_engine().file
        if file is None:
            raise CapabilityNotSupportedError(
                self._engine, Capability.FILE_LIST,
            )
        return file

    @property
    def bash(self) -> BashService:
        """Active engine's BashService.

        Raises CapabilityNotSupportedError when the engine doesn't support
        bash execution. The HTTP route in `api/bash/router.py` short-circuits
        via `check_capability(BASH_EXEC)` before touching this.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        plugin = self._require_engine().bash
        if plugin is None:
            raise CapabilityNotSupportedError(self._engine, Capability.BASH_EXEC)
        return plugin

    @property
    def node(self) -> NodeService:
        """Active engine's NodeService.

        Raises CapabilityNotSupportedError when the engine doesn't expose
        a node registry. The HTTP route in `api/node/router.py` short-
        circuits via `check_capability(NODE_LIST)` before touching this.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        node = self._require_engine().node
        if node is None:
            raise CapabilityNotSupportedError(
                self._engine, Capability.NODE_LIST,
            )
        return node

    @property
    def models(self) -> ModelsService:
        """Active engine's ModelsService.

        Raises CapabilityNotSupportedError when the engine doesn't expose a
        model catalogue. The HTTP route in `api/models/router.py` short-circuits
        before touching this via `check_capability(MODEL_LIST)`, so callers
        normally see a 501 from the guard rather than this error.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        models = self._require_engine().models
        if models is None:
            raise CapabilityNotSupportedError(self._engine, Capability.MODEL_LIST)
        return models

    @property
    def approval(self) -> ApprovalService:
        """Active engine's ApprovalService.

        Raises CapabilityNotSupportedError when the engine doesn't expose a
        session-level approval policy (ApprovalService is optional on the
        Engine Protocol). The HTTP route in `api/approvals.py` short-circuits
        before touching this via `check_capability(APPROVAL_GET/SET)`, so
        callers normally see a 501 from the guard rather than this error.
        """
        from engine.community.core.engine.capability import Capability
        from engine.community.core.engine.exceptions import CapabilityNotSupportedError

        approval = self._require_engine().approval
        if approval is None:
            raise CapabilityNotSupportedError(self._engine, Capability.APPROVAL_GET)
        return approval

    async def on_connection_open(
        self, auth: AuthContext | None = None,
    ) -> None:
        """Notify the active engine that an inbound WS connection opened."""
        await self._require_engine().on_connection_open(auth)

    async def on_connection_close(
        self, auth: AuthContext | None = None,
    ) -> None:
        """Notify the active engine that an inbound WS connection closed."""
        await self._require_engine().on_connection_close(auth)

    def _require_engine(self) -> Engine:
        if self._active_engine is None:
            raise RuntimeError(
                "EngineManager has no active engine; call await initialize() first"
            )
        return self._active_engine

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """启动时初始化：启动引擎进程、激活引擎、启动 cron 服务"""
        from engine.community.process import create_engine_process

        log.info(f"Initializing engine: {self._engine}")
        self._init_phase = "starting"
        self._last_init_error = None

        # 1. 启动引擎进程（限时 300s，避免 openclaw 配置错误导致 Engine 无法启动）
        self._process = create_engine_process(self._engine)
        try:
            await asyncio.wait_for(self._process.start(), timeout=300)
        except TimeoutError:
            self._last_init_error = "Engine process not ready after 300s"
            log.warning(
                "Engine process not ready after 300s, continuing startup anyway. "
                "Chat features may be unavailable until the engine process is up."
            )
        except Exception as e:
            self._last_init_error = f"Engine process failed to start: {e!r}"
            log.warning(
                "Engine process failed to start: %s. "
                "Continuing startup anyway. "
                "Chat features may be unavailable until the engine process is up.",
                e,
            )
        finally:
            # We've attempted the subprocess start and waited on it. Any
            # subsequent step that raises will overwrite this with "failed".
            self._init_phase = "start_attempted"

        # 2. 激活 Engine（registry lookup + initialize + validate_capabilities）
        try:
            await self._activate_engine(self._engine)
        except Exception:
            self._init_phase = "failed"
            raise

        # 3. Persist active-engine marker
        self._save_last_engine(self._engine)

        # 4. 启动 cron 服务（轮询 + monitor）
        await self._start_cron_services()

        self._init_phase = "completed"

    async def switch(self, target: str, force: bool = False) -> dict:
        """
        切换到目标引擎。

        Args:
            target: 目标引擎名称（必须在 registry 中注册）
            force: 是否强制切换（忽略活跃连接）

        Returns:
            切换结果 dict

        Raises:
            ValueError: target 未注册
            RuntimeError: 有活跃连接且非 force
        """
        # 先做 alias 规范化：允许调用方传 "aicoding" / "claude_code" / "ClaudeCode"，
        # 避免上层每次都要知道 canonical 拼写。未知名字维持原样，落到下面的
        # registry 白名单校验 —— 没注册就直接报 ValueError。
        from engine.community.core.engine.naming import normalize

        canonical = normalize(target) or target
        target = canonical

        if not self._registry.has(target):
            raise ValueError(
                f"Unsupported engine type: {target}. "
                f"Registered: {list(self._registry.names())}"
            )

        if target == self._engine:
            return {
                "switched": False,
                "engine": self._engine,
                "reason": "already active",
            }

        async with self._lock:
            old_engine = self._engine
            log.info(f"Switching engine: {old_engine} -> {target} (force={force})")
            self._set_transition(
                operation="switching",
                from_engine=old_engine,
                to_engine=target,
                phase="validating",
            )
            self._init_phase = "starting"
            self._last_init_error = None

            try:
                active = self._get_active_connections()
                if active > 0 and not force:
                    raise RuntimeError(
                        f"有 {active} 个活跃 WebSocket 连接，使用 force=true 强制切换"
                    )

                # 停止 cron 服务（先停 cron，对齐 shutdown() 的顺序 —
                # 和 startup 顺序严格相反：先拆最外层的后台服务，再拆引擎）
                self._set_transition_phase("stopping_cron")
                await self._stop_cron_services()

                self._set_transition_phase("deactivating_engine")
                await self._deactivate_engine()

                self._set_transition_phase("starting_target_process")
                from engine.community.process import create_engine_process

                new_process = create_engine_process(target)
                try:
                    await new_process.start()
                finally:
                    self._init_phase = "start_attempted"

                # TODO: Cross-engine state cleanup is deferred until the
                # multi-engine design work — see
                # `project_engine_switch_cleanup.md` in memory.
                sync_result: dict = {}

                self._set_transition_phase("resetting_server")
                self._reset_server()

                self._set_transition_phase("stopping_previous_process")
                if self._process:
                    await self._process.stop()

                self._set_transition_phase("updating_engine")
                self._process = new_process
                # The manager owns the live active-engine slot; a switch just
                # mutates it. No write-back to a config global: under DI the
                # manager singleton is the single source of truth for the
                # current engine, and a fresh process re-seeds from
                # EngineConfig.default_engine (runtime switches are soft state).
                self._engine = target

                self._set_transition_phase("activating_engine")
                await self._activate_engine(target)

                # 启动新引擎的 cron 服务
                self._set_transition_phase("starting_cron")
                await self._start_cron_services()

                # 保存当前引擎标记
                self._save_last_engine(target)

                self._set_transition_phase("finalizing")
                log.info(f"Engine switched: {old_engine} -> {target}")
                self._init_phase = "completed"
                return {
                    "switched": True,
                    "engine": target,
                    "previous": old_engine,
                    "sync": sync_result,
                }
            except Exception as e:
                self._set_transition_error(str(e))
                self._init_phase = "failed"
                raise
            finally:
                self._clear_transition()

    async def restart(self, force: bool = False) -> dict:
        async with self._lock:
            engine = self._engine
            log.info(f"Restarting engine: {engine} (force={force})")
            self._set_transition(
                operation="restarting",
                from_engine=engine,
                to_engine=engine,
                phase="validating",
            )
            self._init_phase = "starting"
            self._last_init_error = None
            try:
                active = self._get_active_connections()
                if active > 0 and not force:
                    raise RuntimeError(
                        f"有 {active} 个活跃 WebSocket 连接，使用 force=true 强制重启"
                    )

                # 停止 cron 服务（先停 cron，对齐 shutdown() 的顺序 —
                # 和 startup 顺序严格相反：先拆最外层的后台服务，再拆引擎）
                self._set_transition_phase("stopping_cron")
                await self._stop_cron_services()

                self._set_transition_phase("deactivating_engine")
                await self._deactivate_engine()

                self._set_transition_phase("resetting_server")
                self._reset_server()

                self._set_transition_phase("restarting_process")
                from engine.community.process import create_engine_process
                if self._process is None:
                    self._process = create_engine_process(engine)
                try:
                    await self._process.restart()
                finally:
                    self._init_phase = "start_attempted"

                self._set_transition_phase("activating_engine")
                await self._activate_engine(engine)

                # 重启 cron 服务
                self._set_transition_phase("restarting_cron")
                await self._start_cron_services()

                self._set_transition_phase("finalizing")
                log.info(f"Engine restarted: {engine}")
                self._init_phase = "completed"
                return {"restarted": True, "engine": engine}
            except Exception as e:
                self._set_transition_error(str(e))
                self._init_phase = "failed"
                raise
            finally:
                self._clear_transition()

    async def shutdown(self) -> None:
        """优雅关闭：停止 cron 服务、关闭引擎、停止进程"""
        log.info("Shutting down engine")

        await self._stop_cron_services()
        await self._deactivate_engine()

        if self._process:
            await self._process.stop()

        log.info("Engine shut down")

    # ── Active engine construction / teardown ──────────────────────────────

    async def _activate_engine(self, name: str) -> None:
        """Look up, construct, initialize, and validate the engine with `name`."""
        cls = self._registry.get(name)
        instance = cls()
        try:
            await instance.initialize()
        except Exception as e:
            self._last_init_error = repr(e)
            log.exception(f"Engine {name!r} initialize() failed")
            raise
        # validate_capabilities is defined on BaseEngine; structural Engine
        # implementations may skip it.
        validate = getattr(instance, "validate_capabilities", None)
        if callable(validate):
            try:
                validate()
            except Exception as e:
                self._last_init_error = repr(e)
                raise
        self._active_engine = instance
        log.info(f"Engine activated: {name}")

    async def _deactivate_engine(self) -> None:
        """Call shutdown() on the active engine (if any) and clear the slot."""
        if self._active_engine is None:
            return
        try:
            await self._active_engine.shutdown()
        except Exception as e:
            log.warning(f"Engine shutdown failed: {e}")
        finally:
            self._active_engine = None

    async def status(self) -> dict:
        """返回当前引擎状态"""
        process_status: dict[str, Any] = {}
        if self._process is not None and hasattr(self._process, "status"):
            try:
                process_status = self._process.status()
            except Exception as e:
                process_status = {"running": False, "last_error": str(e)}
        if self._process is not None and hasattr(self._process, "is_running"):
            try:
                process_status["running"] = await self._process.is_running()
            except Exception as e:
                process_status["running"] = False
                process_status["last_error"] = str(e)
        return {
            "engine": self._engine,
            "active_connections": self._get_active_connections(),
            "process": process_status,
            "transition": self._transition_snapshot(),
        }

    # 4×2 lookup over (lifecycle phase, subprocess liveness) producing the
    # FE-visible {state, message} pair. The mapping is the contract; do not
    # add compound conditions on _last_init_error here — the lifecycle phase
    # is already the manager's verdict.
    #
    # Reachability today: FastAPI's startup hook runs initialize() to
    # completion before the HTTP server starts serving, so external probes
    # can only land on `completed × {alive,dead}`. The `starting` and
    # `start_attempted` rows are reachable only if initialize() is ever made
    # non-blocking; the `failed` rows are reachable only via mid-life
    # switch()/restart() failures (the server is already up by then). Kept
    # as the full table so the contract is stable across those scenarios.
    _READINESS_TABLE: dict[tuple[str, bool], tuple[str, str]] = {
        ("starting", True): ("starting", "引擎已启动，初始化中"),
        ("starting", False): ("starting", "引擎启动中"),
        ("start_attempted", True): ("starting", "引擎已启动，初始化中"),
        ("start_attempted", False): ("starting", "引擎启动中，耗时异常"),
        ("completed", True): ("ready", "启动完成，服务中"),
        ("completed", False): ("engine_unavailable", "启动已结束，引擎异常"),
        ("failed", True): ("failed", "引擎已启动，初始化失败"),
        ("failed", False): ("failed", "失败"),
    }

    async def readiness(self) -> dict[str, Any]:
        """Return the engine's FE-visible readiness as {state, message}.

        Derived from a 4×2 lookup over (`_init_phase`, subprocess liveness).
        See `_READINESS_TABLE` for the mapping. The probe layer in backend
        wraps this with two extra states (`unhealthy`, `unknown`) for
        transport-level failures.
        """
        process_running = False
        if self._process is not None and hasattr(self._process, "is_running"):
            try:
                process_running = await self._process.is_running()
            except Exception:
                process_running = False

        # Defensive fallback: an unrecognized phase shouldn't exist, but if
        # it does we surface "starting" rather than crash — the FE will keep
        # waiting and the wrong phase will get noticed in logs.
        state, message = self._READINESS_TABLE.get(
            (self._init_phase, process_running),
            ("starting", "引擎启动中"),
        )
        return {"state": state, "message": message}

    # ── Cron 服务管理 ──────────────────────────────────────────────────────

    async def _start_cron_services(self) -> None:
        """Start the engine-agnostic cron polling worker.

        Engine-specific cron-side workers (e.g. OpenClaw's SystemEvent
        replacement monitor) live inside the engine's own `initialize()` and
        are not the manager's concern.
        """
        try:
            from engine.community.core.cron.services.notify import make_notify_callback
            from engine.community.core.cron.services.polling import CronPollingService

            cron_api = self.cron
            self._cron_polling = CronPollingService(
                engine=self._engine,
                cron_api=cron_api,
                notify_callback=(
                    make_notify_callback(self._notification_service)
                    if self._notification_service is not None
                    else None
                ),
            )
            await self._cron_polling.start()
            log.info(f"Cron polling started for {self._engine}")
        except Exception as e:
            log.error(f"Failed to start cron polling: {e}")

    async def _stop_cron_services(self) -> None:
        """Stop the engine-agnostic cron polling worker."""
        if self._cron_polling is not None:
            try:
                await self._cron_polling.stop()
                log.info("Cron polling stopped")
            except Exception as e:
                log.warning(f"Failed to stop cron polling: {e}")
            self._cron_polling = None

    def _save_last_engine(self, engine: str) -> None:
        """保存当前引擎"""
        from engine.community.core.cron.constants import LAST_ENGINE_FILE

        LAST_ENGINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_ENGINE_FILE.write_text(engine)

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _get_active_connections(self) -> int:
        """Count active inbound WebSocket connections on the generic server.

        Phase C unified every engine behind ``engine.community.api.transport.ws_server``, so
        this no longer branches on the active engine name. Returns 0 when
        the server hasn't been constructed yet (process still warming up).
        """
        try:
            from engine.community.api.transport.ws_server import get_server
            server = get_server()
            return len(server._connections)
        except Exception:
            return 0

    def _reset_server(self) -> None:
        """Drop WebSocket server singletons across engine switch/restart.

        Resets both the engine-agnostic server (per-connection AuthContext
        cache) and the AiCoding-specific server (plugin / client references)
        so stale state doesn't leak across engine boundaries.
        """
        try:
            from engine.community.api.transport.ws_server import reset_server
            reset_server()
        except Exception as e:
            log.warning(f"Reset generic server failed: {e}")
        try:
            from engine.community.api.transport.ws_server import run_server_resets
            run_server_resets()
        except Exception as e:
            log.warning(f"Reset registered servers failed: {e}")
        log.info("Servers reset")

    def _set_transition(
        self,
        operation: str,
        from_engine: str,
        to_engine: str | None,
        phase: str,
    ) -> None:
        self._transition = {
            "operation": operation,
            "phase": phase,
            "from_engine": from_engine,
            "to_engine": to_engine,
            "started_at_ms": int(time.time() * 1000),
            "error": None,
        }

    def _set_transition_phase(self, phase: str) -> None:
        if self._transition is None:
            return
        self._transition["phase"] = phase

    def _set_transition_error(self, error: str) -> None:
        if self._transition is None:
            return
        self._transition["error"] = error

    def _clear_transition(self) -> None:
        self._transition = None

    def _transition_snapshot(self) -> dict[str, Any] | None:
        if self._transition is None:
            return None
        return dict(self._transition)
