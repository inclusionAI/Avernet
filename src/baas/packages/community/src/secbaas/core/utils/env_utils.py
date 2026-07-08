"""
环境工具模块

提供环境判断、本机 IP 获取等工具函数
"""

import os
import socket

from secbaas.config import ConfigLoader


def is_sofa_mode() -> bool:
    return os.getenv("SECBAAS_SOFA_MODE", "true").lower() not in (
        "0",
        "false",
        "no",
    )


def _is_debug() -> bool:
    config = ConfigLoader.load()
    app_config = config.user_config.get("app", {})
    return bool(app_config.get("local_debug", False))


def is_empty_env() -> bool:
    env = (
        os.getenv("SERVER_ENV")
        or os.getenv("REAL_SERVER_ENV")
        or os.getenv("ALIPAY_APP_ENV")
        or ""
    )
    env = env.lower()
    return not env


def is_dev() -> bool:
    env = (
        os.getenv("SERVER_ENV")
        or os.getenv("REAL_SERVER_ENV")
        or os.getenv("ALIPAY_APP_ENV")
        or ""
    )
    env = env.lower()
    return not env or env in ["stable", "dev"]


def is_local_debug() -> bool:
    return (is_empty_env() or is_dev()) and _is_debug()


def get_current_env() -> str:
    """获取当前系统环境标识。

    Returns:
        环境："dev", "pre", 或 "prod"
    """
    env = (
        os.getenv("SERVER_ENV")
        or os.getenv("REAL_SERVER_ENV")
        or os.getenv("ALIPAY_APP_ENV")
        or ""
    )
    env = env.lower()

    if env in ["prod", "gray"]:
        return "prod"
    elif env in ["pre", "prepub"]:
        return "pre"
    else:
        return "dev"


def get_current_env_with_gray() -> str:
    """获取当前系统环境标识（区分灰度和线上）。

    Returns:
        环境："dev", "pre", "gray", 或 "prod"
    """
    env = (
        os.getenv("SERVER_ENV")
        or os.getenv("REAL_SERVER_ENV")
        or os.getenv("ALIPAY_APP_ENV")
        or ""
    )
    env = env.lower()

    if env == "prod":
        return "prod"
    elif env == "gray":
        return "gray"
    elif env in ["pre", "prepub"]:
        return "pre"
    else:
        return "dev"


def get_local_ip() -> str:
    """获取当前机器 IP 地址。

    通过创建 UDP socket 连接来获取本机 IP 地址，该方法获取的是本机对外的实际 IP，
    而非 127.0.0.1 或 localhost。

    Returns:
        本机 IP 地址字符串，如果获取失败则返回 "127.0.0.1"
    """
    import logging

    logger = logging.getLogger("env_utils")

    try:
        # 创建一个 UDP socket，通过连接外部地址来获取本机 IP
        # 注意：不会真正发送数据包，只是路由选择
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return str(ip)
    except Exception as e:
        logger.warning(f"Failed to get local IP: {e}, using 127.0.0.1")
        return "127.0.0.1"
