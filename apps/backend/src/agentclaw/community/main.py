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
import sys

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


def _require_local_profile(profile: DeployProfile) -> None:
    """Reject a corp/prod profile — it must boot via agentclaw/corp/main.py."""
    if profile not in _LOCAL_PROFILES:
        raise RuntimeError(
            f"DEPLOY_PROFILE={profile.value!r} is a corp/prod profile and is not "
            "supported by the community entrypoint; boot the backend via "
            "agentclaw/corp/main.py instead."
        )


if __name__ == "__main__":  # pragma: no cover - entrypoint wiring; profile gate tested via _require_local_profile
    from agentclaw.community._entry import ensure_stdin_fd
    ensure_stdin_fd()

    import argparse
    parser = argparse.ArgumentParser(description="AgentClaw Backend")
    parser.add_argument("--config", "-c", type=str, default=None,
                        help="Configuration file path")

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

        # Configure root logger (no company LogManager in this mode).
        logging.basicConfig(
            level=logging.DEBUG,
            format=(
                '%(asctime)s - %(name)s - %(levelname)s'
                ' - [%(process)d] - [%(processName)s] - %(message)s'
            ),
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
        logger.info("runtime config (local)")
        logger.info(json.dumps(config.model_dump(), ensure_ascii=False, indent=2))

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
        uvicorn.run(app, host=_host, port=_port)
    else:
        # Corp / prod profiles boot via the company runner from the corp
        # entrypoint, which is not part of the community distribution. Fail fast
        # rather than silently mis-booting.
        _require_local_profile(_profile)
