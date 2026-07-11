"""
Local Process Manager — manages per-bot adapter + openclaw process pairs.

This is the local equivalent of the Arca SDK. In production, ArcaDeviceService
delegates to the Arca SDK to create/destroy/query sandboxes. Locally,
LocalDeviceService delegates to this manager for the same lifecycle:

  Arca SDK                    LocalProcessManager
  ─────────────────────────   ─────────────────────────
  create_sync_sandbox()   →   allocate_ports()
  (bootstrap + start)    →   start()
  sandbox.destroy()      →   stop()
  (shutdown all)         →   stop_all()
  sandbox.get_info()     →   is_healthy() / get_entry()

The manager owns:
  - Port allocation from fixed ranges (20010–20099 for adapters, 18800–18899
    for openclaw gateways)
  - Openclaw config directory creation (per-bot engine root)
  - Process spawning (openclaw gateway + engine adapter)
  - Process tracking (keyed by device_id)
  - Graceful shutdown (SIGTERM → SIGKILL)

Thread Safety
─────────────
``start()`` is called from a background thread (via ``_start_service``
in ``service_local.py``). The main thread may concurrently call ``stop()``
(bot deletion) or ``stop_all()`` (backend shutdown). All mutable state
is protected by ``threading.Lock``.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from secbaas.logger import get_logger

from ._errors import DeviceAllocateError

logger = get_logger("plugin-sandbox-arca-local-proc")

# Port ranges for dynamically allocated processes.
ADAPTER_PORT_START = 20010
ADAPTER_PORT_END = 20099
OPENCLAW_PORT_START = 18800
OPENCLAW_PORT_END = 18899
HERMES_PORT_START = 18700
HERMES_PORT_END = 18799

# How long to wait for a process to exit after SIGTERM before sending SIGKILL.
SIGTERM_WAIT_SEC = 5

# How long to wait for each process to become healthy (seconds).
OPENCLAW_HEALTH_TIMEOUT = 90
ADAPTER_HEALTH_TIMEOUT = 30
HERMES_HEALTH_TIMEOUT = 30

# Interval between TCP health check attempts (seconds)
HEALTH_CHECK_INTERVAL = 0.5

# Path to the openclaw.json config template (relative to project root).
_OPENCLAW_CONFIG_TEMPLATE = "scripts/openclaw.json"

# Path to the hermes.yaml config template (relative to project root).
_HERMES_CONFIG_TEMPLATE = "scripts/hermes.yaml"
_BCN_PLUGIN_REPO_PATH = Path("src") / "plugin" / "packages" / "openclaw-channel-bcn"


def _default_bcn_plugin_path() -> str:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _BCN_PLUGIN_REPO_PATH
        if candidate.is_dir():
            return str(candidate)
    return os.path.expanduser("~/.openclaw/extensions/openclaw-channel-bcn")


def _merge_singlebox_model_config(oc_config: dict) -> None:
    """Merge the repo-local singlebox model config into an OpenClaw config."""
    configured = os.environ.get("SINGLEBOX_MODEL_CONFIG_FILE", "").strip()
    if not configured:
        return

    config_path = Path(configured)
    if not config_path.is_file():
        raise DeviceAllocateError(
            f"SINGLEBOX_MODEL_CONFIG_FILE does not exist: {config_path}"
        )

    try:
        with open(config_path, encoding="utf-8") as f:
            model_config = json.load(f)
    except json.JSONDecodeError as exc:
        raise DeviceAllocateError(
            f"SINGLEBOX_MODEL_CONFIG_FILE is not valid JSON: {config_path}"
        ) from exc

    if not isinstance(model_config, dict):
        raise DeviceAllocateError(
            f"SINGLEBOX_MODEL_CONFIG_FILE must be a JSON object: {config_path}"
        )

    models = model_config.get("models")
    if models is not None:
        oc_config["models"] = models

    defaults = model_config.get("agents", {}).get("defaults", {})
    if isinstance(defaults, dict):
        target_defaults = oc_config.setdefault("agents", {}).setdefault("defaults", {})
        for key in ("model", "models", "imageModel"):
            target_defaults.pop(key, None)
        for key in ("model", "models", "imageModel"):
            if key in defaults:
                target_defaults[key] = defaults[key]


@dataclass
class ProcessEntry:
    """Represents a running adapter (+ optional openclaw/hermes) for one bot.

    ``openclaw_process`` is ``None`` for engines that don't own a local
    gateway (e.g. ``aicoding``). ``hermes_process`` is ``None`` for
    engines other than ``hermes``.
    """

    sandbox_id: str
    device_id: str
    bot_id: str
    adapter_process: subprocess.Popen
    adapter_port: int
    openclaw_process: subprocess.Popen | None = None
    openclaw_port: int = 0
    hermes_process: subprocess.Popen | None = None
    hermes_port: int = 0
    config_dir: Path = field(default_factory=lambda: Path("."))
    workspace_dir: Path = field(default_factory=lambda: Path("."))
    started_at: datetime = field(default_factory=datetime.now)


class LocalProcessManager:
    """Singleton manager for local adapter + openclaw process pairs.

    Usage from local_device_service.py:
        manager = LocalProcessManager.instance()
        adapter_port, openclaw_port = manager.allocate_ports()
        config_dir = manager.create_openclaw_config(...)
        manager.start(device_id=..., ...)
        manager.stop(device_id)
    """

    _instance: Optional["LocalProcessManager"] = None
    _init_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._processes: dict[str, ProcessEntry] = {}
        self._adapter_ports: set[int] = set()
        self._openclaw_ports: set[int] = set()
        self._hermes_ports: set[int] = set()

    @classmethod
    def instance(cls) -> "LocalProcessManager":
        """Get or create the singleton instance. Thread-safe."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    # Defense-in-depth: ensure child processes are cleaned up
                    # even if the FastAPI lifespan shutdown path is not reached
                    # (e.g. crash, unhandled exception, SIGKILL).
                    import atexit

                    atexit.register(cls._instance.stop_all)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton. Only used in tests."""
        # Unregister the atexit handler to avoid calling stop_all()
        # on a stale instance during interpreter shutdown in tests.
        if cls._instance is not None:
            import atexit

            try:
                atexit.unregister(cls._instance.stop_all)
            except Exception:
                pass
        cls._instance = None

    # ──────────────────────────────────────────────────────────────────────
    # Port allocation (called from _do_allocate)
    # ──────────────────────────────────────────────────────────────────────

    def allocate_ports(self, engine: str = "openclaw") -> tuple[int, int]:
        """Allocate a free (adapter_port, engine_port) pair based on engine type."""
        with self._lock:
            adapter_port = self._find_free_port(
                ADAPTER_PORT_START, ADAPTER_PORT_END, self._adapter_ports
            )

            if engine == "openclaw":
                engine_port = self._find_free_port(
                    OPENCLAW_PORT_START, OPENCLAW_PORT_END, self._openclaw_ports
                )
                self._openclaw_ports.add(engine_port)
            elif engine == "hermes":
                engine_port = self._find_free_port(
                    HERMES_PORT_START, HERMES_PORT_END, self._hermes_ports
                )
                self._hermes_ports.add(engine_port)
            else:
                # aicoding and others don't need engine port
                engine_port = 0

            self._adapter_ports.add(adapter_port)
            return adapter_port, engine_port

    # ──────────────────────────────────────────────────────────────────────
    # Config creation (called from _do_allocate)
    # ──────────────────────────────────────────────────────────────────────

    def create_openclaw_config(
        self,
        *,
        bolt_id: str,
        openclaw_port: int,
        workspace_dir: Path,
        entity_id: str = "",
    ) -> Path:
        """Create a per-bot openclaw config directory with customized settings.

        Returns:
            Path to the config directory
        """
        # Align singlebox with the online OpenClaw layout: the per-bot engine
        # root plays the role of /home/admin/.openclaw, containing both
        # openclaw.json and runtime state such as logs/tasks/identity.
        config_dir = workspace_dir.parent
        config_dir.mkdir(parents=True, exist_ok=True)

        config_file = config_dir / "openclaw.json"

        template_path = self._resolve_config_template_path()
        if template_path and template_path.exists():
            with open(template_path) as f:
                oc_config = json.load(f)
        else:
            logger.warning(
                "OpenClaw config template not found at %s, using minimal config",
                template_path,
            )
            oc_config = {}

        _merge_singlebox_model_config(oc_config)

        oc_config.setdefault("gateway", {})["port"] = openclaw_port
        oc_config["gateway"]["mode"] = "local"
        oc_config["gateway"].setdefault("auth", {})["mode"] = "none"
        oc_config.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = (
            str(workspace_dir)
        )

        # singlebox 多 bot: backend 在 per-bot workspace/skills/ 下建了
        # ``skills-repo -> ~/aiworkbench/skills-repo`` 这个全局共享 git repo 软链;
        # openclaw 出于安全考虑默认拒绝「软链 escape configured root」
        # (workspace 内 symlink 指向 root 之外的目标会被丢弃, 报
        # ``reason=symlink-escape``)。docs/tools/skills 给出的官方配置:
        # ``skills.load.allowSymlinkTargets`` 显式信任这些 target, 让 openclaw
        # 接受 skill-repo 下的所有 skill。
        aiworkbench_repo = Path.home() / "aiworkbench" / "skills-repo"
        oc_config.setdefault("skills", {}).setdefault("load", {})[
            "allowSymlinkTargets"
        ] = [str(aiworkbench_repo)]

        # Wire up BCS channel + BCN plugin only when the plugin is built.
        # Without a built plugin, openclaw rejects the "bcs" channel id and
        # the extension entry, causing config validation failure and gateway exit.
        bcn_plugin_path = os.environ.get(
            "BCN_PLUGIN_PATH",
            _default_bcn_plugin_path(),
        )
        bcn_entry_point = Path(bcn_plugin_path) / "dist" / "esm" / "index.js"
        if bcn_entry_point.exists():
            bcs_port = os.environ.get("BCS_PORT", "21000")
            bcs_url = f"ws://127.0.0.1:{bcs_port}/ws/bot"
            bcs_bot_id = f"{bolt_id}:{entity_id}" if entity_id else bolt_id
            oc_config.setdefault("channels", {})["bcs"] = {
                "enabled": True,
                "bcsUrl": bcs_url,
                "botId": bcs_bot_id,
                "capabilities": {
                    "summary": "",
                    "domains": [],
                    "skills": [],
                    "scopes": [],
                },
            }
            oc_config.setdefault("plugins", {})["load"] = {"paths": [bcn_plugin_path]}
            oc_config["plugins"].setdefault("entries", {})["openclaw-channel-bcn"] = {
                "enabled": True
            }
        else:
            logger.info(
                "BCN plugin not built at %s, skipping BCS channel config",
                bcn_entry_point,
            )

        with open(config_file, "w") as f:
            json.dump(oc_config, f, indent=2, ensure_ascii=False)
        config_file.chmod(0o600)

        logger.info(
            "Created openclaw config: %s, port=%s, workspace=%s",
            config_file,
            openclaw_port,
            workspace_dir,
        )
        return config_dir

    def create_hermes_config(
        self,
        *,
        bolt_id: str,
        hermes_port: int,
        workspace_dir: Path,
    ) -> Path:
        """Create a per-bot Hermes config directory with customized settings.

        Returns:
            Path to the config directory
        """
        import yaml

        profile_name = f"bot_{bolt_id}"
        # Support LOCAL_HERMES_DIR env var for sandboxed environments
        hermes_base = os.environ.get("LOCAL_HERMES_DIR")
        if hermes_base:
            config_dir = Path(hermes_base) / f".hermes-{profile_name}"
        else:
            config_dir = Path.home() / f".hermes-{profile_name}"
        config_dir.mkdir(parents=True, exist_ok=True)

        # Create Hermes subdirectories (required by Hermes)
        for subdir in ("sessions", "logs", "skills", "memories", "cron"):
            (config_dir / subdir).mkdir(parents=True, exist_ok=True)

        config_file = config_dir / "config.yaml"

        template_path = self._resolve_hermes_config_template_path()
        if template_path and template_path.exists():
            with open(template_path) as f:
                hermes_config = yaml.safe_load(f) or {}
        else:
            logger.warning(
                "Hermes config template not found at %s, using minimal config",
                template_path,
            )
            hermes_config = {}

        # Set API server port for the dashboard
        hermes_config.setdefault("platforms", {})["api_server"] = {
            "enabled": True,
            "host": "127.0.0.1",
            "port": hermes_port,
        }

        # Write config file
        with open(config_file, "w") as f:
            yaml.dump(hermes_config, f, default_flow_style=False, allow_unicode=True)

        logger.info(
            "Created hermes config: %s, port=%s, workspace=%s",
            config_file,
            hermes_port,
            workspace_dir,
        )
        return config_dir

    # ──────────────────────────────────────────────────────────────────────
    # Process lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def start(
        self,
        *,
        device_id: str,
        bot_id: str,
        adapter_port: int,
        engine_port: int,
        config_dir: Path,
        workspace_dir: Path,
        callback_token: str = "",
        entity_id: str = "",
        symbol_json: str | None = None,
        agent_code: str | None = None,
        engine: str = "openclaw",
        admins: list[str] | None = None,
    ) -> ProcessEntry:
        """Spawn an adapter (+ optional openclaw/hermes) process pair and register them.

        This is the local equivalent of Arca's bootstrap + service start.
        On failure, any spawned processes are killed but ports stay reserved
        (matching Arca behavior where a failed sandbox isn't cleaned up).

        The ``engine`` argument determines which backend the adapter talks to:

        * ``openclaw`` (default) — spawn a per-bot openclaw gateway and point
          the adapter at ``ws://127.0.0.1:<engine_port>``.
        * ``hermes`` — spawn a per-bot hermes dashboard and point the adapter
          at ``http://127.0.0.1:<engine_port>``.
        * ``aicoding`` — skip the engine spawn entirely (teamclaw-aicoding-
          relay is managed externally by ``start_service.sh`` / ops) and
          point the adapter at the relay via ``AICODING_RELAY_URL``.

        Args:
            device_id:      Device identifier (key for later stop)
            bot_id:         Bot identifier
            adapter_port:   Pre-allocated adapter port
            engine_port:    Pre-allocated engine port (openclaw/hermes, unused for aicoding)
            config_dir:     Bot's openclaw config directory
            workspace_dir:  Bot's workspace directory
            callback_token: Token for credentials file
            entity_id:      Entity ID for credentials file
            symbol_json:    JSON string of skill symlink mappings (optional)
            agent_code:     Agent code for credentials file (optional)
            engine:         Engine type (``"openclaw"``, ``"hermes"``, or ``"aicoding"``)

        Returns:
            The registered ProcessEntry

        Raises:
            RuntimeError: if a process fails to start
        """
        logger.info(
            "start() called: device_id=%s bot_id=%s adapter_port=%s engine_port=%s "
            "engine=%s config_dir=%s workspace_dir=%s entity_id=%s "
            "symbol_json=%s agent_code=%s admins=%s",
            device_id,
            bot_id,
            adapter_port,
            engine_port,
            engine,
            config_dir,
            workspace_dir,
            entity_id,
            bool(symbol_json),
            agent_code,
            admins,
        )

        openclaw_process = None
        hermes_process = None
        adapter_process = None
        openclaw_port = 0
        hermes_port = 0

        try:
            # Step 1: Write credentials BEFORE spawning openclaw.
            # The BCN plugin reads ~/.credentials on startup to determine
            # bot_id for BCS connection. If this file doesn't exist when
            # openclaw starts, the plugin sends bot_id=none and BCS
            # assigns a random bot_uuid that won't match the onboard call.
            self._write_credentials(
                device_id=device_id,
                bot_id=bot_id,
                config_dir=config_dir,
                callback_token=callback_token,
                entity_id=entity_id,
                agent_code=agent_code,
                admins=admins,
            )

            # Step 2: Spawn openclaw gateway (openclaw engine only).
            # For aicoding, the relay is managed externally (see
            # engine.community.process.AiCodingProcess and start_service.sh), so we
            # intentionally skip this step.
            if engine == "openclaw":
                openclaw_process = self._spawn_openclaw(
                    bot_id=bot_id,
                    openclaw_port=engine_port,
                    workspace_dir=workspace_dir,
                    config_dir=config_dir,
                )
                openclaw_port = engine_port
            elif engine == "hermes":
                hermes_process = self._spawn_hermes(
                    bot_id=bot_id,
                    hermes_port=engine_port,
                    config_dir=config_dir,
                )
                hermes_port = engine_port
            elif engine in ("aicoding", "claude_code"):
                logger.info(
                    "Skipping openclaw spawn (engine=%s); relay is managed externally",
                    engine,
                )
            else:
                logger.info(
                    "Skipping engine spawn (engine=%s); relay is managed externally",
                    engine,
                )

            # Step 3: Spawn engine adapter
            # (credentials already written in step 1)
            adapter_process = self._spawn_adapter(
                adapter_port=adapter_port,
                engine_port=engine_port,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                engine=engine,
            )

            # Step 4: Register
            sandbox_id = f"arca-local-proc-{uuid.uuid4()}"
            self._register(
                sandbox_id=sandbox_id,
                device_id=device_id,
                bot_id=bot_id,
                adapter_process=adapter_process,
                adapter_port=adapter_port,
                openclaw_process=openclaw_process,
                openclaw_port=openclaw_port,
                hermes_process=hermes_process,
                hermes_port=hermes_port,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
            )

            # Step 5: Set up skill symlinks (non-fatal)
            if symbol_json:
                self._setup_skills(adapter_port, symbol_json)

            logger.info(
                "Local container started: device=%s adapter=:%s engine=%s engine_port=:%s bot=%s",
                device_id,
                adapter_port,
                engine,
                engine_port,
                bot_id,
            )
            return self._processes[sandbox_id]

        except Exception:
            # Kill any processes spawned before the failure.
            for proc in [adapter_process, openclaw_process, hermes_process]:
                if proc is not None and proc.poll() is None:
                    proc.kill()
            raise

    def stop(self, device_id: str) -> bool:
        """Kill a bot's process pair and free its ports.

        Three-phase: remove entry (lock), kill processes (no lock), free ports (lock).
        """
        with self._lock:
            entry = self._processes.pop(device_id, None)
            if entry is None:
                logger.warning(
                    "No process entry for device %s, nothing to stop", device_id
                )
                return True

        self._kill_process(entry.adapter_process, f"adapter(port={entry.adapter_port})")
        self._kill_process(
            entry.openclaw_process, f"openclaw(port={entry.openclaw_port})"
        )
        self._kill_process(entry.hermes_process, f"hermes(port={entry.hermes_port})")

        with self._lock:
            self._adapter_ports.discard(entry.adapter_port)
            self._openclaw_ports.discard(entry.openclaw_port)
            self._hermes_ports.discard(entry.hermes_port)

        logger.info(
            "Stopped processes: device=%s adapter=:%s openclaw=:%s hermes=:%s",
            device_id,
            entry.adapter_port,
            entry.openclaw_port,
            entry.hermes_port,
        )
        return True

    def stop_all(self) -> None:
        """Kill all tracked process pairs. Called on backend shutdown."""
        with self._lock:
            entries = list(self._processes.values())
            self._processes.clear()

        for entry in entries:
            self._kill_process(
                entry.adapter_process, f"adapter(port={entry.adapter_port})"
            )
            self._kill_process(
                entry.openclaw_process, f"openclaw(port={entry.openclaw_port})"
            )
            self._kill_process(
                entry.hermes_process, f"hermes(port={entry.hermes_port})"
            )

        with self._lock:
            self._adapter_ports.clear()
            self._openclaw_ports.clear()
            self._hermes_ports.clear()

        if entries:
            logger.info("Stopped %d process pair(s)", len(entries))

    # ──────────────────────────────────────────────────────────────────────
    # Query
    # ──────────────────────────────────────────────────────────────────────

    def is_healthy(self, device_id: str) -> bool:
        """Check if tracked processes for a device are still running.

        Optional openclaw/hermes slots (``None``) are treated as healthy.
        """
        with self._lock:
            entry = self._processes.get(device_id)
        if entry is None:
            return False
        adapter_alive = entry.adapter_process.poll() is None
        openclaw_alive = (
            entry.openclaw_process is None or entry.openclaw_process.poll() is None
        )
        hermes_alive = (
            entry.hermes_process is None or entry.hermes_process.poll() is None
        )
        return adapter_alive and openclaw_alive and hermes_alive

    def get_entry(self, device_id: str) -> ProcessEntry | None:
        """Look up a process entry by device_id."""
        with self._lock:
            return self._processes.get(device_id)

    # ──────────────────────────────────────────────────────────────────────
    # Internal: process spawning
    # ──────────────────────────────────────────────────────────────────────

    def _spawn_openclaw(
        self,
        *,
        bot_id: str,
        openclaw_port: int,
        workspace_dir: Path,
        config_dir: Path,
    ) -> subprocess.Popen:
        """Spawn an openclaw gateway process and wait for it to be healthy.

        Uses OPENCLAW_STATE_DIR and OPENCLAW_CONFIG_PATH to isolate state.
        """
        profile_name = f"bot_{bot_id}"
        log_path = config_dir / "gateway.log"
        config_file = config_dir / "openclaw.json"

        logger.info(
            "Spawning openclaw gateway: profile=%s, port=%s, config_dir=%s, log=%s",
            profile_name,
            openclaw_port,
            config_dir,
            log_path,
        )

        env = {**os.environ}
        env["OPENCLAW_WORKSPACE_DIR"] = str(workspace_dir)
        env["OPENCLAW_GATEWAY_TOKEN"] = ""
        env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
        # Tell openclaw where to find config and store state
        env["OPENCLAW_CONFIG_PATH"] = str(config_file)
        env["OPENCLAW_STATE_DIR"] = str(config_dir)

        log_fh = open(log_path, "a")
        try:
            process = subprocess.Popen(
                [
                    "openclaw",
                    "gateway",
                    "run",
                    "--port",
                    str(openclaw_port),
                ],
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
        finally:
            log_fh.close()

        if not self._wait_for_health(openclaw_port, OPENCLAW_HEALTH_TIMEOUT):
            raise RuntimeError(
                f"OpenClaw gateway failed to start on port {openclaw_port}. "
                f"Check {log_path} for details."
            )

        logger.info(
            "OpenClaw gateway healthy: port=%s, pid=%s", openclaw_port, process.pid
        )
        return process

    def _spawn_hermes(
        self,
        *,
        bot_id: str,
        hermes_port: int,
        config_dir: Path,
    ) -> subprocess.Popen:
        """Spawn a per-bot Hermes Dashboard process."""
        log_path = config_dir / "hermes.log"

        logger.info(
            "Spawning hermes dashboard: bot=%s, port=%s, config_dir=%s, log=%s",
            bot_id,
            hermes_port,
            config_dir,
            log_path,
        )

        env = {**os.environ}
        # HERMES_HOME points to the config directory where config.yaml resides.
        # Hermes will look for $HERMES_HOME/config.yaml and use it for settings.
        env["HERMES_HOME"] = str(config_dir)
        # Enable WebSocket API (/api/ws) for external clients like engine adapter.
        # Without this flag, Hermes Dashboard returns 403 for WebSocket connections.
        env["HERMES_DASHBOARD_TUI"] = "1"

        log_fh = open(log_path, "a")
        try:
            process = subprocess.Popen(
                [
                    "hermes",
                    "dashboard",
                    "--port",
                    str(hermes_port),
                    "--no-open",
                    "--tui",  # Enable embedded TUI/chat with WebSocket API
                ],
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
        finally:
            log_fh.close()

        if not self._wait_for_hermes_health(hermes_port, timeout=HERMES_HEALTH_TIMEOUT):
            raise RuntimeError(
                f"Hermes Dashboard failed to start on port {hermes_port}. "
                f"Check {log_path} for details."
            )

        logger.info(
            "Hermes Dashboard healthy: port=%s, pid=%s", hermes_port, process.pid
        )
        return process

    def _wait_for_hermes_health(self, port: int, timeout: float = 30.0) -> bool:
        """Wait for Hermes Dashboard to become healthy via HTTP GET."""
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{port}/"
        session = requests.Session()
        session.trust_env = False

        try:
            while time.monotonic() < deadline:
                try:
                    resp = session.get(url, timeout=2)
                    if resp.status_code == 200:
                        return True
                except requests.RequestException:
                    pass
                time.sleep(HEALTH_CHECK_INTERVAL)
        finally:
            session.close()

        return False

    def _spawn_adapter(
        self,
        *,
        adapter_port: int,
        engine_port: int,
        config_dir: Path,
        workspace_dir: Path,
        engine: str = "openclaw",
    ) -> subprocess.Popen:
        """Spawn an engine adapter (FastAPI/uvicorn) wired to the selected backend.

        ``CHAT_ENGINE`` env var picks the engine plugin stack.
        """
        engine_src_dir = self._resolve_engine_src_dir()
        engine_python = self._resolve_engine_python(engine_src_dir)
        log_path = config_dir / "adapter.log"

        env = {**os.environ}
        env["SERVER_PORT"] = str(adapter_port)
        env["SERVER_HOST"] = "127.0.0.1"
        env["CHAT_ENGINE"] = engine
        env["CREDENTIALS_PATH"] = str(config_dir / ".credentials")
        # Disable zero-check
        env["ZERO_CHECK_ENABLED"] = "false"
        no_proxy_hosts = []
        for key in ("NO_PROXY", "no_proxy"):
            for host in env.get(key, "").split(","):
                host = host.strip()
                if host and host not in no_proxy_hosts:
                    no_proxy_hosts.append(host)
        for host in ("localhost", "127.0.0.1", "::1"):
            if host not in no_proxy_hosts:
                no_proxy_hosts.append(host)
        no_proxy = ",".join(no_proxy_hosts)
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy
        # Singlebox per-bot workspace 根目录;adapter 进程的 _convert_path
        # 和 skill 模块都读这个 env 决定路径根。详见
        # docs/superpowers/specs/2026-06-10-engine-per-bot-workspace-design.md §4.3.A3
        env["OPENCLAW_WORKSPACE_DIR"] = str(workspace_dir)

        if engine == "openclaw":
            # Numeric loopback avoids macOS system proxies intercepting local
            # WebSocket handshakes when localhost is not in the bypass list.
            env["OPENCLAW_GATEWAY_URL"] = f"ws://127.0.0.1:{engine_port}"
            env["OPENCLAW_GATEWAY_TOKEN"] = ""

            # Disable adapter's built-in engine process management.
            # Empty START_CMD → EngineProcessSettings.enabled=False (config.py:78)
            # → CommandEngineProcess.start() skips spawning (process.py:57-62).
            env["ENGINE_OPENCLAW_PROCESS_START_CMD"] = ""
            env["ENGINE_OPENCLAW_PROCESS_STOP_CMD"] = ""
            env["ENGINE_OPENCLAW_PROCESS_RESTART_CMD"] = ""
            env["ENGINE_OPENCLAW_PROCESS_HEALTHCHECK_TCP"] = f"127.0.0.1:{engine_port}"
        elif engine == "hermes":
            # Set Hermes Dashboard URL for the adapter.
            # Engine adapter will connect to Hermes Dashboard via HTTP/WebSocket.
            env["HERMES_URL"] = f"http://127.0.0.1:{engine_port}"
        elif engine == "aicoding":
            # teamclaw-aicoding-relay is managed externally (start_service.sh).
            # Respect an operator-provided AICODING_RELAY_URL; otherwise fall
            # back to the relay's default port 18900 (matches
            # engine.aicoding.config._DEFAULT_RELAY_URL).
            env.setdefault("AICODING_RELAY_URL", "ws://127.0.0.1:18900")
        elif engine == "claude_code":
            # Claude Code engine uses the same relay pattern as aicoding.
            # Respect an operator-provided CLAUDE_CODE_RELAY_URL; otherwise
            # fall back to the relay's default port 18900.
            env.setdefault("CLAUDE_CODE_RELAY_URL", "ws://127.0.0.1:18900")
            logger.info(
                "Claude Code engine env: CLAUDE_CODE_RELAY_URL=%s",
                env["CLAUDE_CODE_RELAY_URL"],
            )

        extra_skills_dir = workspace_dir / ".extra-skills"
        extra_skills_dir.mkdir(parents=True, exist_ok=True)
        env["SKILLS_LINK_BASE_DIR"] = str(extra_skills_dir)

        env["PYTHONPATH"] = str(engine_src_dir)

        if engine == "openclaw":
            logger.info(
                "Spawning engine adapter: engine=%s port=%s, openclaw_url=ws://127.0.0.1:%s, log=%s",
                engine,
                adapter_port,
                engine_port,
                log_path,
            )
        elif engine == "hermes":
            logger.info(
                "Spawning engine adapter: engine=%s port=%s, hermes_url=http://127.0.0.1:%s, log=%s",
                engine,
                adapter_port,
                engine_port,
                log_path,
            )
        else:
            relay_url = env.get("AICODING_RELAY_URL") or env.get(
                "CLAUDE_CODE_RELAY_URL", ""
            )
            logger.info(
                "Spawning engine adapter: engine=%s port=%s, relay_url=%s, log=%s",
                engine,
                adapter_port,
                relay_url,
                log_path,
            )

        log_fh = open(log_path, "a")
        try:
            process = subprocess.Popen(
                [
                    engine_python,
                    "-m",
                    "uvicorn",
                    "engine.community.api.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(adapter_port),
                ],
                env=env,
                cwd=str(engine_src_dir),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
        finally:
            log_fh.close()

        if not self._wait_for_health(adapter_port, ADAPTER_HEALTH_TIMEOUT):
            raise RuntimeError(
                f"Engine adapter failed to start on port {adapter_port}. "
                f"Check {log_path} for details."
            )

        logger.info(
            "Engine adapter healthy: port=%s, pid=%s", adapter_port, process.pid
        )
        return process

    # ──────────────────────────────────────────────────────────────────────
    # Internal: registration
    # ──────────────────────────────────────────────────────────────────────

    def _register(
        self,
        *,
        sandbox_id: str,
        device_id: str,
        bot_id: str,
        adapter_process: subprocess.Popen,
        adapter_port: int,
        openclaw_process: subprocess.Popen | None = None,
        openclaw_port: int = 0,
        hermes_process: subprocess.Popen | None = None,
        hermes_port: int = 0,
        config_dir: Path = Path("."),
        workspace_dir: Path = Path("."),
    ) -> None:
        """Record a successfully spawned process pair."""
        with self._lock:
            self._processes[sandbox_id] = ProcessEntry(
                sandbox_id=sandbox_id,
                device_id=device_id,
                bot_id=bot_id,
                adapter_process=adapter_process,
                adapter_port=adapter_port,
                openclaw_process=openclaw_process,
                openclaw_port=openclaw_port,
                hermes_process=hermes_process,
                hermes_port=hermes_port,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
            )

    # ──────────────────────────────────────────────────────────────────────
    # Internal: helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _write_credentials(
        *,
        device_id: str,
        bot_id: str,
        config_dir: Path,
        callback_token: str,
        entity_id: str,
        agent_code: str | None = None,
        admins: list[str] | None = None,
    ) -> None:
        """Write a .credentials file for the adapter process.

        In production, start_service.sh creates /home/admin/.credentials.
        """
        lines = [
            f"TOKEN={callback_token}",
            f"CLIENT_ID={device_id}",
            f"OWNER_ID={entity_id}",
            f"BOT_ID={bot_id}",
            f"AGENT_CODE={agent_code or bot_id}",
        ]
        if admins:
            lines.append(f"ADMINS={','.join(admins)}")
        content = "\n".join(lines) + "\n"
        credentials_path = config_dir / ".credentials"
        credentials_path.write_text(content)
        credentials_path.chmod(0o600)

        # Also write ~/.credentials so the BCN plugin (openclaw-channel-bcn)
        # can discover the bot_id. The plugin hardcodes ~/.credentials and does
        # not read CREDENTIALS_PATH. Without this, the plugin sends
        # bot_id=none, BCS assigns a random bot_uuid, and onboard fails with
        # "Bot 未在协作网络注册" because the bot_uuid doesn't match.
        home_credentials = Path.home() / ".credentials"
        home_credentials.write_text(content)
        home_credentials.chmod(0o600)

    @staticmethod
    def _setup_skills(adapter_port: int, symbol_json: str) -> None:
        """Set up skill symlinks by calling the adapter's skills endpoint."""
        try:
            symlink_mappings = json.loads(symbol_json)
            if not symlink_mappings:
                return

            symlinks = []
            for mapping in symlink_mappings:
                source = mapping.get("source", "")
                target = mapping.get("target", "")
                if source and target:
                    symlinks.append({"source": source, "target": target})

            if not symlinks:
                return

            url = f"http://127.0.0.1:{adapter_port}/api/skills/symlink"
            response = requests.post(url, json={"symlinks": symlinks}, timeout=10)
            if response.status_code == 200:
                logger.info(
                    "Set up %d skill symlink(s) via adapter :%s",
                    len(symlinks),
                    adapter_port,
                )
            else:
                logger.warning(
                    "Skills symlink setup returned %s: %s",
                    response.status_code,
                    response.text,
                )
        except Exception as e:
            logger.warning("Failed to set up skill symlinks: %s", e)

    @staticmethod
    def _wait_for_health(port: int, timeout: float) -> bool:
        """Wait for a process to start accepting TCP connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(1.0)
                result = sock.connect_ex(("127.0.0.1", port))
                if result == 0:
                    return True
            except Exception:
                pass
            finally:
                sock.close()
            time.sleep(HEALTH_CHECK_INTERVAL)
        return False

    @staticmethod
    def _resolve_config_template_path() -> Path | None:
        """Find the openclaw.json config template by walking up to the project root."""
        current = Path(__file__).resolve()
        for _ in range(10):
            candidate = current / _OPENCLAW_CONFIG_TEMPLATE
            if candidate.exists():
                return candidate
            current = current.parent
        return None

    @staticmethod
    def _resolve_hermes_config_template_path() -> Path | None:
        """Find the hermes.yaml config template by walking up to the project root."""
        current = Path(__file__).resolve()
        for _ in range(10):
            candidate = current / _HERMES_CONFIG_TEMPLATE
            if candidate.exists():
                return candidate
            current = current.parent
        return None

    @staticmethod
    def _resolve_engine_src_dir() -> Path:
        """Resolve the path to src/engine/src/."""
        configured = os.environ.get("LOCAL_ENGINE_SRC_DIR")
        if configured:
            candidate = Path(configured).expanduser().resolve()
            if candidate.exists():
                return candidate
            raise DeviceAllocateError(
                f"Configured LOCAL_ENGINE_SRC_DIR does not exist: {candidate}"
            )

        current = Path(__file__).resolve().parent
        for _ in range(16):
            candidate = current / "src" / "engine" / "src"
            if candidate.exists():
                return candidate
            current = current.parent
        raise DeviceAllocateError(
            "Could not find engine source directory (src/engine/src/). "
            "Is the project structure intact?"
        )

    @staticmethod
    def _resolve_engine_python(engine_src_dir: Path) -> str:
        """Resolve the Python executable for the engine adapter.

        Prefers the engine's own venv, falls back to current interpreter.
        """
        engine_venv = engine_src_dir.parent / ".venv" / "bin" / "python"
        if engine_venv.exists():
            return str(engine_venv)
        logger.warning(
            "Engine venv not found at %s, using current Python: %s",
            engine_venv,
            sys.executable,
        )
        return sys.executable

    @staticmethod
    def _find_free_port(start: int, end: int, already_allocated: set[int]) -> int:
        """Find the first available port in [start, end]."""
        for port in range(start, end + 1):
            if port in already_allocated:
                continue
            if _is_port_available(port):
                return port
        raise DeviceAllocateError(f"No free port available in range {start}-{end}")

    @staticmethod
    def _kill_process(proc: subprocess.Popen | None, label: str) -> None:
        """Gracefully terminate a process, escalating to SIGKILL if needed.

        Tolerates ``proc=None`` for optional engine slots.
        """
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=SIGTERM_WAIT_SEC)
                logger.info(
                    "Process %s terminated gracefully (pid=%s)", label, proc.pid
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "Process %s still alive after SIGKILL (pid=%s)", label, proc.pid
                    )
                else:
                    logger.warning(
                        "Process %s killed forcefully (pid=%s)", label, proc.pid
                    )
        except Exception as e:
            logger.error("Error killing process %s (pid=%s): %s", label, proc.pid, e)


def _is_port_available(port: int) -> bool:
    """Check if a port is available by attempting a TCP connection."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.3)
        result = sock.connect_ex(("127.0.0.1", port))
        return result != 0
    except Exception:
        return True
    finally:
        sock.close()
