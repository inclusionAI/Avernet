"""Lifecycle Protocol — 统一组件启停接口。

实现此 Protocol 的 bean 会被自动纳入 ``lifecycle_components`` 有序列表，
由 ``initialize_services`` / ``shutdown_services`` 按序启停。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from secbaas.logger import get_logger

from ._configs import DatabaseConfig

logger = get_logger("bootstrap-lifecycle")


@runtime_checkable
class Lifecycle(Protocol):
    """组件生命周期协议。

    - ``start()``: 启动组件（含初始化、后台任务等）。
    - ``stop()``: 优雅停止组件。

    统一为 async；同步实现通过适配方法桥接。
    """

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class LocalProcessManagerLifecycle:
    """Lifecycle adapter for LocalProcessManager (optional plugin).

    When the ``arca.local_proc`` plugin is not installed, ``start()`` and
    ``stop()`` are no-ops.  When installed, ``stop()`` calls
    ``LocalProcessManager.instance().stop_all()``.
    """

    async def start(self) -> None:
        """No-op: processes are started on-demand, not at container init."""

    async def stop(self) -> None:
        """Stop all managed adapter/engine process pairs."""
        try:
            from secbaas.plugins.sandbox.arca.local_proc import LocalProcessManager

            LocalProcessManager.instance().stop_all()
            logger.info("LocalProcessManager: all processes stopped")
        except ImportError:
            pass
        except Exception as e:
            logger.error("LocalProcessManager shutdown error: %s", e)


class DatabaseManagerLifecycle:
    """Lifecycle adapter for the global ``db_manager`` singleton.

    ``start()`` resolves the ``plugin_database`` Selector from the DI
    container and delegates initialisation to the plugin's ``init_database``.
    ``stop()`` calls ``db_manager.close()`` to dispose connection pools.
    """

    def __init__(self, db_config: DatabaseConfig) -> None:
        self._db_config = db_config

    async def start(self) -> None:
        """Initialise database engine from the provided config."""
        from secbaas.bootstrap import get_container

        plugin = get_container().plugins.plugin_database()
        plugin.init_database(self._db_config)

    async def stop(self) -> None:
        """Close the database manager (dispose async & sync engines)."""
        try:
            from secbaas.core.database import db_manager

            await db_manager.close()
            logger.info("Database manager closed")
        except Exception as e:
            logger.error("Database manager close error: %s", e)
