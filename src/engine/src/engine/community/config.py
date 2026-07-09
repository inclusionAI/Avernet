"""
Engine 通用配置

包含引擎选择和默认配置值。
各引擎特定配置在各自的子目录中：
- engine/openclaw/config.py
- engine/moltis/config.py
"""

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# 默认连接超时时间（秒）
DEFAULT_CONNECTION_TIMEOUT = 10

CONFIG_DIR = Path(__file__).parent
# 统一配置文件入口（Moltis/OpenClaw 共用）
CONFIG_FILE = CONFIG_DIR / "engine.json"

# 统一 MCP token 配置默认值（保持中性，不绑定具体引擎）
DEFAULT_MCP_TOKEN_HEADER_NAME = "x-openclaw-enterprise-mcp-token"
DEFAULT_MCP_TOKEN_FORWARD_TO_WSS = True
DEFAULT_MCP_TOKEN_STORE_PATH = "~/.openclaw-enterprise/token-store.json"
DEFAULT_MCP_TOKEN_PERSIST_ENABLED = False
DEFAULT_MCPORTER_CONFIG_PATH = "~/.mcporter/mcporter.json"

# 默认最大连接数（0 表示不限制）
DEFAULT_MAX_CONNECTIONS = 0


@dataclass(frozen=True)
class MCPTokenSettings:
    header_name: str
    forward_to_wss: bool
    store_path: Path
    persist_enabled: bool


@dataclass(frozen=True)
class EngineProcessSettings:
    engine: str
    start_cmd: tuple[str, ...]
    stop_cmd: tuple[str, ...]
    restart_cmd: tuple[str, ...]
    workdir: Optional[Path]
    startup_timeout_sec: float
    graceful_timeout_sec: float
    healthcheck_tcp: Optional[str]
    healthcheck_http: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return len(self.start_cmd) > 0


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _normalize_header_name(value: Any) -> str:
    if isinstance(value, str):
        header_name = value.strip().lower()
        if header_name:
            return header_name
    return DEFAULT_MCP_TOKEN_HEADER_NAME


def _resolve_path(value: Any) -> Path:
    raw = str(value).strip() if value is not None else DEFAULT_MCP_TOKEN_STORE_PATH
    if not raw:
        raw = DEFAULT_MCP_TOKEN_STORE_PATH
    return Path(raw).expanduser().resolve()


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_cmd(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(
            str(item).strip()
            for item in value
            if str(item).strip()
        )
    if isinstance(value, str):
        parsed = shlex.split(value.strip())
        return tuple(parsed)
    return ()


def _read_engine_file_config(path: Path = CONFIG_FILE) -> Dict[str, Any]:
    # 配置文件缺失时回退到环境变量和默认值，不中断启动
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        # 解析失败时走兜底，避免单个配置问题影响主链路
        pass
    return {}


def _resolve_default_engine(path: Path = CONFIG_FILE) -> str:
    """Pick the default engine name from env var → engine.json → "openclaw".

    The JSON block this reads (per heterogeneous-engine-architecture §18.1):

        {
          "engine": {
            "default": "openclaw",
            "fallback": null
          },
          ...
        }

    Only `engine.default` is consulted here; `engine.fallback` is reserved for
    future EngineManager fallback logic (M3+).
    """
    env_value = os.getenv("CHAT_ENGINE", "").strip()
    if env_value:
        return env_value
    file_data = _read_engine_file_config(path)
    engine_block = file_data.get("engine") or {}
    if isinstance(engine_block, dict):
        default = engine_block.get("default")
        if isinstance(default, str) and default.strip():
            return default.strip()
    return "openclaw"


def _read_process_block(file_data: Dict[str, Any], engine: str) -> Dict[str, Any]:
    """Pick the per-engine process block from new schema, falling back to legacy.

    New (§18.1):  file_data["engines"][engine]["process"]
    Legacy:       file_data["process"][engine]

    TODO(M3b): once every engine.json in production has been rewritten to the
    new `engines.<name>.process` shape and no deploy script reads the legacy
    `process.<name>` path, delete the fallback branch below and inline the new
    lookup back into `load_engine_process_settings`.
    """
    engines_block = file_data.get("engines") or {}
    if isinstance(engines_block, dict):
        engine_block = engines_block.get(engine) or {}
        if isinstance(engine_block, dict):
            process_block = engine_block.get("process") or {}
            if isinstance(process_block, dict) and process_block:
                return process_block

    # Legacy fallback — remove with TODO above.
    process_data = file_data.get("process") or {}
    if isinstance(process_data, dict):
        legacy_block = process_data.get(engine) or {}
        if isinstance(legacy_block, dict):
            return legacy_block
    return {}


def load_engine_process_settings(
    engine: str,
    path: Path = CONFIG_FILE,
) -> EngineProcessSettings:
    """Load `EngineProcessSettings` for `engine` from engine.json + env overrides.

    Reads the new §18.1 schema (`engines.<name>.process.*`) first, falls back
    to the legacy flat schema (`process.<name>.*`) if the new block is absent.
    A missing block in both yields all-empty settings — `enabled` is False, so
    `process.py` treats it as a no-op (relay-style engine with no subprocess).

    Sample engine.json (both shapes recognized):

        {
          "engines": {                            // ← new (§18.1) — preferred
            "openclaw": {
              "process": {
                "start_cmd": [...],
                "startup_timeout_sec": 600,
                "healthcheck_tcp": "127.0.0.1:18789"
              }
            }
          },
          "process": {                            // ← legacy — fallback
            "openclaw": { "start_cmd": [...] }
          }
        }
    """
    normalized_engine = engine.strip().lower()

    file_data = _read_engine_file_config(path)
    engine_data = _read_process_block(file_data, normalized_engine)

    env_prefix = f"ENGINE_{normalized_engine.upper()}_PROCESS_"
    start_cmd = _parse_cmd(
        os.getenv(f"{env_prefix}START_CMD", engine_data.get("start_cmd", []))
    )
    stop_cmd = _parse_cmd(
        os.getenv(f"{env_prefix}STOP_CMD", engine_data.get("stop_cmd", []))
    )
    restart_cmd = _parse_cmd(
        os.getenv(f"{env_prefix}RESTART_CMD", engine_data.get("restart_cmd", []))
    )
    workdir_raw = os.getenv(
        f"{env_prefix}WORKDIR",
        engine_data.get("workdir"),
    )
    workdir = None
    if workdir_raw:
        workdir = Path(str(workdir_raw)).expanduser().resolve()

    startup_timeout_sec = _to_float(
        os.getenv(
            f"{env_prefix}STARTUP_TIMEOUT_SEC",
            engine_data.get("startup_timeout_sec", 10),
        ),
        10.0,
    )
    graceful_timeout_sec = _to_float(
        os.getenv(
            f"{env_prefix}GRACEFUL_TIMEOUT_SEC",
            engine_data.get("graceful_timeout_sec", 5),
        ),
        5.0,
    )
    healthcheck_tcp = os.getenv(
        f"{env_prefix}HEALTHCHECK_TCP",
        engine_data.get("healthcheck_tcp"),
    )
    healthcheck_tcp = (
        str(healthcheck_tcp).strip() if healthcheck_tcp is not None else None
    )
    if healthcheck_tcp == "":
        healthcheck_tcp = None

    healthcheck_http = os.getenv(
        f"{env_prefix}HEALTHCHECK_HTTP",
        engine_data.get("healthcheck_http"),
    )
    healthcheck_http = (
        str(healthcheck_http).strip() if healthcheck_http is not None else None
    )
    if healthcheck_http == "":
        healthcheck_http = None

    return EngineProcessSettings(
        engine=normalized_engine,
        start_cmd=start_cmd,
        stop_cmd=stop_cmd,
        restart_cmd=restart_cmd,
        workdir=workdir,
        startup_timeout_sec=startup_timeout_sec,
        graceful_timeout_sec=graceful_timeout_sec,
        healthcheck_tcp=healthcheck_tcp,
        healthcheck_http=healthcheck_http,
    )


def load_mcp_token_settings(path: Path = CONFIG_FILE) -> MCPTokenSettings:
    # 优先级：环境变量 > engine.json > 默认值
    file_data = _read_engine_file_config(path)

    header_name = _normalize_header_name(
        os.getenv(
            "ENGINE_MCP_TOKEN_HEADER_NAME",
            file_data.get("mcp_token_header_name", DEFAULT_MCP_TOKEN_HEADER_NAME),
        )
    )
    forward_to_wss = _to_bool(
        os.getenv(
            "ENGINE_MCP_TOKEN_FORWARD_TO_WSS",
            file_data.get("mcp_token_forward_to_wss", DEFAULT_MCP_TOKEN_FORWARD_TO_WSS),
        ),
        DEFAULT_MCP_TOKEN_FORWARD_TO_WSS,
    )
    store_path = _resolve_path(
        os.getenv(
            "ENGINE_MCP_TOKEN_STORE_PATH",
            file_data.get("mcp_token_store_path", DEFAULT_MCP_TOKEN_STORE_PATH),
        )
    )
    persist_enabled = _to_bool(
        os.getenv(
            "ENGINE_MCP_TOKEN_PERSIST_ENABLED",
            file_data.get("mcp_token_persist_enabled", DEFAULT_MCP_TOKEN_PERSIST_ENABLED),
        ),
        DEFAULT_MCP_TOKEN_PERSIST_ENABLED,
    )

    return MCPTokenSettings(
        header_name=header_name,
        forward_to_wss=forward_to_wss,
        store_path=store_path,
        persist_enabled=persist_enabled,
    )


def load_mcporter_config_path(path: Path = CONFIG_FILE) -> Path:
    file_data = _read_engine_file_config(path)
    configured = os.getenv(
        "ENGINE_MCPORTER_CONFIG_PATH",
        file_data.get("mcporter_config_path", DEFAULT_MCPORTER_CONFIG_PATH),
    )
    raw = str(configured).strip() if configured is not None else DEFAULT_MCPORTER_CONFIG_PATH
    if not raw:
        raw = DEFAULT_MCPORTER_CONFIG_PATH
    return Path(raw).expanduser().resolve()


def load_max_connections(path: Path = CONFIG_FILE) -> int:
    """从配置文件加载最大连接数"""
    file_data = _read_engine_file_config(path)
    value = file_data.get("max_connections", DEFAULT_MAX_CONNECTIONS)
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONNECTIONS


# ── DingTalk 配置 ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DingTalkSettings:
    access_key_id: str
    access_key_secret: str
    robot_code: str


def load_dingtalk_settings(path: Path = CONFIG_FILE) -> Optional[DingTalkSettings]:
    """加载钉钉配置（优先级：环境变量 > engine.json > None）"""
    file_data = _read_engine_file_config(path)
    dingtalk_data = file_data.get("dingtalk", {})
    if not isinstance(dingtalk_data, dict):
        dingtalk_data = {}

    access_key_id = os.getenv(
        "ENGINE_DINGTALK_ACCESS_KEY_ID",
        dingtalk_data.get("access_key_id", ""),
    )
    access_key_secret = os.getenv(
        "ENGINE_DINGTALK_ACCESS_KEY_SECRET",
        dingtalk_data.get("access_key_secret", ""),
    )
    robot_code = os.getenv(
        "ENGINE_DINGTALK_ROBOT_CODE",
        dingtalk_data.get("robot_code", ""),
    )

    if not access_key_id or not access_key_secret or not robot_code:
        return None

    return DingTalkSettings(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        robot_code=robot_code,
    )


# ── Immutable config object (F1 — DI) ───────────────────────────────────────
#
# `EngineConfig` is the single immutable snapshot the composition root builds
# once at startup and injects. It replaced the module-global `get_*`/`set_*`/
# `reset_*` accessors (removed in F1 Task 31); the pure stateless `load_*`
# readers above remain (used by `load_engine_config` + the per-engine
# `EngineProcessSettings` resolver). Per-engine process settings stay
# parameterized via `load_engine_process_settings(engine)` and are bound
# separately, so they are not embedded here.


@dataclass(frozen=True)
class EngineConfig:
    """Immutable, process-wide engine configuration snapshot.

    Built once via `load_engine_config()` at startup and injected. Being a
    frozen dataclass, an existing instance never changes when the environment
    later mutates; a *fresh* `load_engine_config()` call reflects current env.

    `default_engine` is the **startup default** — the engine to activate when
    the process boots, resolved from `CHAT_ENGINE` / engine.json. It is NOT a
    live tracker of the currently-active engine: that is mutable runtime (soft)
    state owned by `EngineManager` (`EngineManager.engine` / `.current()`), which
    `switch()` mutates. Ask the manager "what's active now?"; ask the config
    "what did we boot with?". On a fresh process the manager re-seeds from this
    default — runtime switches are not persisted.
    """

    default_engine: str
    max_connections: int
    mcp_token: MCPTokenSettings
    mcporter_config_path: Path
    dingtalk: Optional[DingTalkSettings]


def load_engine_config() -> EngineConfig:
    """Read all engine configuration sources once and return an `EngineConfig`.

    Uses the `load_*` / resolver readers (not the cached `get_*` accessors) so
    the result is a clean snapshot of the current environment + engine.json.
    `default_engine` uses `_resolve_default_engine()` (env → engine.json →
    "openclaw"), independent of any runtime `switch()` the global may hold.
    """
    return EngineConfig(
        default_engine=_resolve_default_engine(),
        max_connections=load_max_connections(),
        mcp_token=load_mcp_token_settings(),
        mcporter_config_path=load_mcporter_config_path(),
        dingtalk=load_dingtalk_settings(),
    )
