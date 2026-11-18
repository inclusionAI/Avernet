#!/usr/bin/env python3
"""AgentClaw community backend entry point (corp-free).

Starts the FastAPI app directly with uvicorn for the community/local profiles
(SQLite + in-memory infrastructure, no MOSN). This entrypoint names no
corp/internal package.

The corp/prod profile boots via the company runner from a separate corp
entrypoint (``agentclaw/corp/main.py``); it is not handled here. Launching this
entrypoint under the ``corp`` profile fails fast with a pointer to it.
"""

import json
import logging
import os
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agentclaw.community.di import DeployProfile, validate_deploy_environment
from agentclaw.community.log import get_logger

logger = get_logger()

# Community/local profiles boot the FastAPI app directly with uvicorn; a corp/prod
# profile boots the company runner from agentclaw/corp/main.py instead.
_LOCAL_PROFILES = (
    DeployProfile.SINGLEBOX,
    DeployProfile.TEST,
    DeployProfile.CORP_TEST,
    DeployProfile.COMMUNITY,
)


_DEFAULT_APP_NAME = "agentclaw"
_APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s"
    " - [%(process)d] - [%(processName)s] - %(message)s"
)
_LOG_FORMATTER = logging.Formatter(_LOG_FORMAT)
_RUNTIME_CONFIG_LOGGER = "agentclaw.runtime-config"
_REDACTED_FOR_LOG = "<redacted>"
_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "passwd",
    "pwd",
    "credential",
    "credentials",
    "private_key",
    "api_key",
    "access_key",
    "signature",
    "hmac",
    "apikey",
    "secretkey",
    "accesskey",
)
_SENSITIVE_QUERY_KEYS = {
    "key",
    "auth",
    "authorization",
    "pwd",
    "sig",
    "apikey",
    "secretkey",
    "accesskey",
    "token",
    "password",
    "secret",
    "api_key",
    "access_key",
    "signature",
}


def _redact_url_credentials(value: str) -> str:
    """Redact URL userinfo and sensitive query parameters when present."""
    if "@" in value and "://" not in value:
        return "<redacted-url>"
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    hostname = parts.hostname or ""
    host = (
        f"[{hostname}]"
        if ":" in hostname and not hostname.startswith("[")
        else hostname
    )
    if port is not None:
        host = f"{host}:{port}"
    netloc = host
    if parts.username is not None or parts.password is not None:
        netloc = f"{_REDACTED_FOR_LOG}@{host}"
    query = parts.query
    if query:
        query = urlencode(
            [
                (
                    key,
                    (
                        _REDACTED_FOR_LOG
                        if _is_secret_key(key, ())
                        or key.strip().lower() in _SENSITIVE_QUERY_KEYS
                        else value
                    ),
                )
                for key, value in parse_qsl(query, keep_blank_values=True)
            ],
            doseq=True,
        )
    fragment = parts.fragment
    if "=" in fragment:
        fragment = urlencode(
            [
                (
                    key,
                    (
                        _REDACTED_FOR_LOG
                        if _is_secret_key(key, ())
                        or key.strip().lower() in _SENSITIVE_QUERY_KEYS
                        else value
                    ),
                )
                for key, value in parse_qsl(fragment, keep_blank_values=True)
            ],
            doseq=True,
        )
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def _is_secret_key(key: str, path: tuple[str, ...]) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).strip().lower()
    if normalized.endswith("_name") or normalized in {
        "secret_name",
        "api_key_prefix",
    }:
        return False
    compact = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    for marker in _SECRET_KEY_MARKERS:
        if (
            compact == marker
            or compact.startswith(f"{marker}_")
            or compact.endswith(f"_{marker}")
            or f"_{marker}_" in compact
        ):
            return True
    return compact == "value" and "header_rules" in path


def _redact_runtime_config_for_log(
    value: object,
    path: tuple[str, ...] = (),
) -> object:
    """Copy a config dump and replace literal credentials and secret values."""
    if isinstance(value, dict):
        redacted: dict[object, object] = {}
        for key, child in value.items():
            child_path = path + (str(key),)
            if _is_secret_key(str(key), path):
                redacted[key] = _REDACTED_FOR_LOG
                continue
            if isinstance(child, str) and (
                str(key).lower() == "url" or str(key).lower().endswith("_url")
            ):
                redacted[key] = _redact_url_credentials(child)
            else:
                redacted[key] = _redact_runtime_config_for_log(child, child_path)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_runtime_config_for_log(item, path) for item in value]
    return value


def _safe_app_name(config_dump: dict) -> str:
    raw_name = config_dump.get("app_name") if isinstance(config_dump, dict) else None
    app_name = str(raw_name).strip() if raw_name is not None else ""
    if _APP_NAME_RE.fullmatch(app_name):
        return app_name
    logger.warning(
        "invalid app_name for log path; using %s fallback", _DEFAULT_APP_NAME
    )
    return _DEFAULT_APP_NAME


def _configured_log_dir(config_dump: dict) -> str:
    env_dir = (os.getenv("AGENTCLAW_LOG_DIR") or "").strip()
    if env_dir:
        return env_dir
    log_config = (
        config_dump.get("log_config") if isinstance(config_dump, dict) else None
    )
    configured = log_config.get("log_dir") if isinstance(log_config, dict) else None
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return ""


def _resolve_log_dir(config_dump: dict) -> Path:
    configured = _configured_log_dir(config_dump)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "logs" / _safe_app_name(config_dump)).resolve()


def _file_handlers(log_dir: Path, app_name: str) -> list[logging.Handler]:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = []
    specifications = (
        (f"{app_name}.log", logging.DEBUG),
        (f"{app_name}-error.log", logging.ERROR),
        (f"{app_name}-fatal.log", logging.CRITICAL),
        ("common-error.log", logging.ERROR),
    )
    try:
        for filename, level in specifications:
            handler = TimedRotatingFileHandler(
                log_dir / filename,
                when="midnight",
                backupCount=7,
                encoding="utf-8",
            )
            handler.setLevel(level)
            handler.setFormatter(_LOG_FORMATTER)
            handlers.append(handler)
        return handlers
    except BaseException:
        for handler in handlers:
            handler.close()
        raise


def _build_logging_handlers(config_dump: dict) -> tuple[Path, list[logging.Handler]]:
    app_name = _safe_app_name(config_dump)
    log_dir = _resolve_log_dir(config_dump)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG)
    console.setFormatter(_LOG_FORMATTER)
    return log_dir, [console, *_file_handlers(log_dir, app_name)]


def _configure_logging(config_dump: dict) -> Path:
    log_dir, handlers = _build_logging_handlers(config_dump)
    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)
    logger.info(
        "logging to %s (app=%s, rotation=midnight x7, timezone=process-local)",
        log_dir,
        _safe_app_name(config_dump),
    )
    return log_dir


def _log_runtime_config_console_only(config_dump: dict) -> None:
    config_logger = logging.getLogger(_RUNTIME_CONFIG_LOGGER)
    for handler in config_logger.handlers[:]:
        config_logger.removeHandler(handler)
        handler.close()
    config_logger.propagate = False
    config_logger.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(_LOG_FORMATTER)
    config_logger.addHandler(console)
    config_logger.info("runtime config (local)")
    config_logger.info(
        json.dumps(
            _redact_runtime_config_for_log(config_dump),
            ensure_ascii=False,
            indent=2,
        )
    )


def _require_local_profile(profile: DeployProfile) -> None:
    """Reject a corp/prod profile — it must boot via agentclaw/corp/main.py."""
    if profile not in _LOCAL_PROFILES:
        raise RuntimeError(
            f"DEPLOY_PROFILE={profile.value!r} is a corp/prod profile and is not "
            "supported by the community entrypoint; boot the backend via "
            "agentclaw/corp/main.py instead."
        )


if (
    __name__ == "__main__"
):  # pragma: no cover - entrypoint wiring; profile gate tested via _require_local_profile
    from agentclaw.community._entry import ensure_stdin_fd

    ensure_stdin_fd()

    import argparse

    parser = argparse.ArgumentParser(description="AgentClaw Backend")
    parser.add_argument(
        "--config", "-c", type=str, default=None, help="Configuration file path"
    )

    args = parser.parse_args()

    # NOTE(totalfrank): the profile is read here to choose the boot path and
    # again in adapters/http/app.py to wire the injector. ``detect()`` is a pure
    # env read so the duplication is harmless; app.py is the composition root the
    # uvicorn ``app`` object goes through.
    _profile = DeployProfile.detect()
    validate_deploy_environment()
    env = (
        os.getenv("SERVER_ENV")
        or os.getenv("REAL_SERVER_ENV")
        or os.getenv("ALIPAY_APP_ENV")
        or ""
    ).lower()
    if _profile in _LOCAL_PROFILES:
        # =====================================================================
        # 本地 / 社区模式：直接启动 FastAPI（无 MOSN）
        # =====================================================================

        # Keep an early stderr sink: config loading itself can fail (for example
        # on a missing env placeholder) before the profile-owned log path is known.
        logging.basicConfig(
            level=logging.DEBUG,
            format=_LOG_FORMAT,
            handlers=[logging.StreamHandler(sys.stderr)],
        )

        logger.info("env: %s", env)
        logger.info("Starting in LOCAL mode (no MOSN)")

        # Bypass the system HTTP/HTTPS proxy for loopback targets.
        # httpx + urllib pick up macOS sysconf proxy settings (ClashX/Surge
        # etc) even with no env vars set, and without NO_PROXY they'll
        # tunnel every backend→adapter call through the proxy — which
        # rejects localhost with HTTP 503. Setting NO_PROXY here makes
        # httpx mount `all://{host}` = None bypass entries. A deployment that
        # maps extra hostnames to loopback appends them via NO_PROXY_EXTRA.
        _loopback_bypass = "localhost,127.0.0.1,::1"
        _extra = os.environ.get("NO_PROXY_EXTRA")
        if _extra:
            _loopback_bypass = f"{_loopback_bypass},{_extra}"
        for _var in ("NO_PROXY", "no_proxy"):
            _existing = os.environ.get(_var)
            os.environ[_var] = (
                f"{_loopback_bypass},{_existing}" if _existing else _loopback_bypass
            )

        # 1. Select the Profile-owned YAML overlay before the first config read.
        # The HTTP composition root repeats this registration when it is imported
        # so direct ASGI imports keep the same bootstrap contract.
        from agentclaw.community.di.config_bootstrap import register_config_provider

        register_config_provider(_profile)

        from agentclaw.community.core.config.sofa import sofa_config as config

        config_dump = config.model_dump()
        _configure_logging(config_dump)
        _log_runtime_config_console_only(config_dump)

        # 2. Start uvicorn directly.
        # Schema bootstrap, event-listener subscription, and every other
        # startup hook run through the Lifecycle Protocol dispatched by
        # ``_app_lifespan`` in adapters/http/app.py (R11). Nothing to
        # register here.
        #
        # The listen address is env-driven so a container can be told which port
        # to serve without a rebuild; the defaults are what the local scripts and
        # every existing launch site already use, so an unset environment behaves
        # exactly as before.
        import uvicorn
        from agentclaw.community.adapters.http.app import app

        _host = os.getenv("BACKEND_HOST") or "0.0.0.0"
        _port = int(os.getenv("BACKEND_PORT") or 8888)
        logger.info("Starting uvicorn on %s:%d", _host, _port)
        uvicorn.run(app, host=_host, port=_port, log_config=None)
    else:
        # Corp / prod profiles boot via the company runner from the corp
        # entrypoint, which is not part of the community distribution. Fail fast
        # rather than silently mis-booting.
        _require_local_profile(_profile)
