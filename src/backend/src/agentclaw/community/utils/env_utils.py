import os
import socket


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
    transitional shim never crashes an ad-hoc import. Safe to call at any startup
    stage (no enum import, no sofapy dependency).

    TEMPORARY: this shim exists only for the remaining direct callers
    (``core/workspace/path_factory.py`` + ``skill_center/services/
    skill_set_service.py``); removed once they stop branching on "am I local".
    """
    return (os.getenv("DEPLOY_PROFILE") or "").strip().lower() in {
        "test",
        "singlebox",
        "corp_test",
    }


def is_singlebox() -> bool:
    """Single Box 模式 — env=singlebox 时返 True。

    Single Box 是独立于 dev/pre/prod 之外的第四档：完全无外网依赖，
    本地 + CI 同一份配置（application-singlebox.yaml）。

    跟 is_local_mode() 的区别：
    - is_local_mode() 看 DEPLOY_PROFILE（test / singlebox → DI 走 LOCAL stub 列）
    - is_singlebox() 看 SERVER_ENV / REAL_SERVER_ENV / ALIPAY_APP_ENV
      （决定加载哪个 application-{env}.yaml）

    实际启动时两者一般同时为 True（local_setup.sh start_backend
    设了 DEPLOY_PROFILE=singlebox + SERVER_ENV=singlebox），但概念上独立。
    """
    return _resolve_env() == "singlebox"


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
    """dev family — dev / stable / singlebox / 空环境 都算 dev。

    singlebox 加入 dev family 是「向后兼容」决策：业务代码大量用
    ``if is_dev():`` 走 dev 路径（开 debug log、跳过 prod-only 校验等），
    singlebox 的行为应该和 dev 一致。
    """
    env = _resolve_env()
    return not env or env in ['stable', 'dev', 'singlebox']


def get_current_env() -> str:
    """获取当前系统环境标识。

    Returns:
        环境: "dev" | "pre" | "prod" | "singlebox"

    singlebox 单独成档（不再 fallback 到 dev），让业务代码可以用
    get_current_env() == "singlebox" 做细粒度判断。
    """
    env = _resolve_env()

    if env in ['prod', 'gray']:
        return "prod"
    elif env in ['pre', 'prepub']:
        return "pre"
    elif env == 'singlebox':
        return "singlebox"
    else:
        return "dev"


def get_current_env_with_gray() -> str:
    """获取当前系统环境标识（区分灰度和线上）。

    Returns:
        环境: "dev" | "pre" | "gray" | "prod" | "singlebox"
    """
    env = _resolve_env()

    if env == 'prod':
        return "prod"
    elif env == 'gray':
        return "gray"
    elif env in ['pre', 'prepub']:
        return "pre"
    elif env == 'singlebox':
        return "singlebox"
    else:
        return "dev"
