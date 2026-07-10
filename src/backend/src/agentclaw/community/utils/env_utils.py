import os
import socket


_LOCAL_DEPLOY_PROFILES = frozenset({"test", "singlebox", "corp_test"})


def _resolve_env() -> str:
    """Single source for resolving runtime env from env vars.

    优先级：SERVER_ENV > REAL_SERVER_ENV > ALIPAY_APP_ENV > ""
    全部统一 .lower()，避免大小写不一致 bug。
    """
    env = os.getenv('SERVER_ENV') or os.getenv('REAL_SERVER_ENV') or os.getenv('ALIPAY_APP_ENV') or ""
    return env.lower()


def is_local_mode() -> bool:
    """Whether the deploy profile uses LOCAL infrastructure stubs.

    Derived from the single ``DEPLOY_PROFILE`` switch (B1): the LOCAL-stub
    profiles are ``test``, ``singlebox`` and ``corp_test`` (B11 — the corp test
    profile is the same LOCAL-stub doubles as ``test`` plus corp modules, so it
    is local mode too). Lenient on unset (returns False) — the strict mandatory
    check lives in ``DeployProfile.detect()`` at the composition root, so this
    transitional shim never crashes an ad-hoc import. This low-level utility
    intentionally checks canonical profile strings instead of importing the DI
    package: importing a composition-root package here creates a reverse
    dependency and a cold-start cycle. Implementation installation remains the
    responsibility of ``DeployProfile`` at the composition root.

    TEMPORARY: this shim exists only for the remaining direct callers
    (``core/workspace/path_factory.py`` + ``skill_center/services/
    skill_set_service.py``); removed once they stop branching on "am I local".
    """
    profile = (os.getenv("DEPLOY_PROFILE") or "").strip().lower()
    return _LOCAL_DEPLOY_PROFILES.__contains__(profile)


def is_empty_env() -> bool:
    return not _resolve_env()


# ---------- 服务器标识 ----------

def _init_server_host() -> str:
    """首次调用时获取 hostname，之后不再变。"""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


_SERVER_HOST: str = _init_server_host()


def get_server_host() -> str:
    """获取当前服务器主机名（进程级缓存，启动后不变）。"""
    return _SERVER_HOST


def is_dev():
    """Return whether the resolved data environment belongs to the dev family."""
    env = _resolve_env()
    return not env or env in ["stable", "dev"]


def get_current_env() -> str:
    """Return the normalized data environment: dev, pre, or prod."""
    env = _resolve_env()
    if env in ["prod", "gray"]:
        return "prod"
    if env in ["pre", "prepub"]:
        return "pre"
    return "dev"


def get_current_env_with_gray() -> str:
    """Return the normalized data environment while preserving gray."""
    env = _resolve_env()
    if env == "prod":
        return "prod"
    if env == "gray":
        return "gray"
    if env in ["pre", "prepub"]:
        return "pre"
    return "dev"
