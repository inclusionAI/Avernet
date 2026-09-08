"""Register community plugin options into the community registry.

Mirrors BAAS plugin registration. Registers the stub
and openai LLM providers under ``llm_provider`` so the container can
resolve one via config.

Agents are intentionally NOT registered here: the candidate agent list is
supplied at runtime by the caller (see ``register_agents``) rather than
hardcoded, so the demo can run with arbitrary agent rosters.
"""

from __future__ import annotations

from typing import Any

from agentcompute.community.bootstrap import get_container

from .._plugin_registry import register_plugin_option
from ..spi._agent import AgentSpec
from ..spi._database import DatabasePlugin
from ..spi._driver import Driver
from ..spi._llm import LLMProviderPlugin
from ..spi._logger import LoggerPlugin
from ..spi._planner import Planner
from ..spi._replanner import Replanner
from ..spi._tracer import TracerPlugin


def register_plugins() -> None:
    register_plugin_option(
        "llm_provider",
        "stub",
        _stub_provider,
    )
    register_plugin_option(
        "llm_provider",
        "openai",
        _openai_provider,
    )
    register_plugin_option(
        "logger",
        "stdlib",
        _stdlib_logger,
    )
    register_plugin_option(
        "tracer",
        "stdlib",
        _stdlib_tracer,
    )
    register_plugin_option(
        "database",
        "sqlite",
        _sqlite_database,
    )
    register_plugin_option("planner", "static", _static_planner)
    register_plugin_option("driver", "static", _static_driver)
    register_plugin_option("driver", "dynamic", _dynamic_driver)
    register_plugin_option("replanner", "dynamic", _dynamic_replanner)


def register_agents(specs: list[AgentSpec]) -> None:
    """Register caller-provided agent specs as ``agent`` plugin options."""
    from .agents._base import LLMBackedAgent

    for spec in specs:
        if spec.metadata.get("type") == "avernet":
            register_plugin_option(
                "agent",
                spec.name,
                lambda spec=spec: _avernet_agent(spec),
            )
        else:
            register_plugin_option(
                "agent",
                spec.name,
                lambda spec=spec: _agent(LLMBackedAgent, spec),
            )


def _stub_provider() -> LLMProviderPlugin:
    from .llm._stub import StubLLMProvider

    return StubLLMProvider()


def _openai_provider() -> LLMProviderPlugin:
    from .llm._openai_compat import OpenAICompatibleProvider

    options = get_container().config.get("llm", {}) or {}
    return OpenAICompatibleProvider(
        base_url=options.get("base_url"),
        api_key=options.get("api_key"),
        model=options.get("model"),
        timeout=int(options.get("timeout", 60)),
        retries=int(options.get("retries", 2)),
    )


def _stdlib_logger() -> LoggerPlugin:
    from .logger._stdlib import StdlibLoggerPlugin

    return StdlibLoggerPlugin()


def _stdlib_tracer() -> TracerPlugin:
    from .tracer._stdlib import StdlibTracerPlugin

    return StdlibTracerPlugin()


def _sqlite_database() -> DatabasePlugin:
    from .database._sqlite import SqliteDatabasePlugin

    options = get_container().config.get("database", {}) or {}
    return SqliteDatabasePlugin(
        database_url=options.get("database_url", "sqlite:///agentcompute.db")
    )


def _agent(agent_cls: Any, spec: AgentSpec) -> Any:
    provider = get_container().plugins().llm_provider()
    return agent_cls(spec=spec, provider=provider)


def _avernet_agent(spec: AgentSpec) -> Any:
    from .agents._avernet import AvernetAgent, AvernetClient

    options = get_container().config.get("avernet", {}) or {}
    required = ["gateway_base_url", "principal_token", "user_id", "manifest_template"]
    missing = [k for k in required if not options.get(k)]
    if missing:
        raise ValueError(f"AvernetAgent requires config.avernet keys: {', '.join(missing)}")
    client = AvernetClient(
        gateway_base_url=str(options["gateway_base_url"]),
        principal_token=str(options["principal_token"]),
        user_id=str(options["user_id"]),
        create_bot_path=str(options.get("create_bot_path", "/openapi/v1/bots/with-manifest")),
        chat_stream_path=str(options.get("chat_stream_path", "/openapi/v1/chat/stream")),
        delete_bot_path=str(options.get("delete_bot_path", "/openapi/v1/bots/{bot_id}")),
        status_path=str(
            options.get("status_path", "/openapi/v1/bots/{bot_id}/with-manifest/status")
        ),
        http_timeout=float(options.get("http_timeout", 60.0)),
        http_retries=int(options.get("http_retries", 2)),
    )
    return AvernetAgent(
        spec=spec,
        client=client,
        manifest_template=str(options["manifest_template"]),
        poll_timeout=float(options.get("poll_timeout", 300.0)),
        poll_interval=float(options.get("poll_interval", 1.0)),
    )


def _static_planner() -> Planner:
    from ._static_planner import StaticPlanner

    return StaticPlanner()


def _static_driver() -> Driver:
    from ._static_driver import StaticDriver

    options = get_container().config.get("driver", {}) or {}
    return StaticDriver(
        max_workers=int(options.get("max_workers", 8)),
        max_retries=int(options.get("max_retries", 3)),
    )


def _dynamic_replanner() -> Replanner:
    from ._dynamic_replanner import DynamicReplanner

    options = get_container().config.get("replanner", {}) or {}
    driver_options = get_container().config.get("driver", {}) or {}
    replan_min_calls = int(driver_options.get("replan_min_calls", 0))
    return DynamicReplanner(
        max_extensions=int(options.get("max_extensions", 10)),
        max_total_nodes=int(options.get("max_total_nodes", 100)),
        replan_timeout_seconds=float(options.get("replan_timeout_seconds", 120.0)),
        max_total_injected_chars=int(options.get("max_total_injected_chars", 8000)),
        inject_completed_results=bool(options.get("inject_completed_results", True)),
        inject_errors=bool(options.get("inject_errors", True)),
        custom_extension_prompt=options.get("custom_extension_prompt"),
        dedup_strategy=str(options.get("dedup_strategy", "skip_dup_goals")),
        force_extension_until_calls=int(
            options.get("force_extension_until_calls", replan_min_calls)
        ),
        max_parse_retries=int(options.get("max_parse_retries", 2)),
    )


def _dynamic_driver() -> Driver:
    from ._dynamic_driver import DynamicDriver

    replanner = get_container().plugins().replanner()
    if replanner is None:
        raise RuntimeError(
            "Config.driver='dynamic' requires Config.replanner to be set "
            "(got None). Set Config.replanner='dynamic' or use driver='static'."
        )
    options = get_container().config.get("driver", {}) or {}
    return DynamicDriver(
        replanner=replanner,
        replan_strategy=str(options.get("replan_strategy", "after_wave")),
        replan_max_calls=int(options.get("replan_max_calls", 10)),
        replan_min_calls=int(options.get("replan_min_calls", 0)),
        replan_cooldown_waves=int(options.get("replan_cooldown_waves", 1)),
        halt_on_replan_error=bool(options.get("halt_on_replan_error", False)),
        persist_replans=bool(options.get("persist_replans", True)),
        max_workers=int(options.get("max_workers", 8)),
        max_retries=int(options.get("max_retries", 3)),
    )
