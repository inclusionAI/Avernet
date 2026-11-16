"""
环境工具模块

提供环境识别和 Worker ID 前缀处理功能。
"""

from __future__ import annotations

import os
import re
import socket
from functools import lru_cache

# Worker ID 格式定义
WORKER_ID_PATTERN = re.compile(r"^wrk_[a-zA-Z0-9_-]+$")

# 环境变量优先级（高优先级在前）
ENV_VARS_PRIORITY = [
    "SERVER_ENV",
    "REAL_SERVER_ENV",
    "ALIPAY_APP_ENV",
    "BCSFUSE_ENV",
    "RUNTIME_MODE",
]


@lru_cache(maxsize=1)
def get_current_env() -> str:
    """获取当前系统环境标识。

    Returns:
        环境标识: "dev", "pre", 或 "prod"
    """
    env = ""
    for var_name in ENV_VARS_PRIORITY:
        env = os.getenv(var_name, "")
        if env:
            break

    env = env.lower()

    if env in ("prod", "production", "online"):
        return "prod"
    elif env in ("pre", "prepub", "staging"):
        return "pre"
    else:
        return "dev"


def is_dev() -> bool:
    """判断是否为开发环境。"""
    return get_current_env() == "dev"


def is_pre() -> bool:
    """判断是否为预发环境。"""
    return get_current_env() == "pre"


def is_prod() -> bool:
    """判断是否为生产环境。"""
    return get_current_env() == "prod"


def add_env_prefix_to_worker_id(worker_id: str) -> str:
    """给 Worker ID 添加环境前缀。

    Args:
        worker_id: 原始 Worker ID，如 "wrk_abc123"

    Returns:
        带环境前缀的 Worker ID，如 "wrk_pre_abc123"

    Raises:
        ValueError: Worker ID 格式不正确
    """
    if not worker_id:
        raise ValueError("Worker ID 不能为空")

    # 如果已经有环境前缀，直接返回
    if re.match(r"^wrk_(dev|pre|prod)_", worker_id):
        return worker_id

    # 验证基础格式
    if not WORKER_ID_PATTERN.match(worker_id):
        raise ValueError(f"Worker ID 格式不正确: {worker_id}")

    # 添加环境前缀
    env = get_current_env()
    return worker_id.replace("wrk_", f"wrk_{env}_", 1)


def get_fusion_env() -> str:
    """获取融合会话表的环境标识值。

    bcsfuse_fusion_session 表通过 env 字段（而非独立表）隔离环境数据：
    - pre 环境: "pre"
    - 其他环境（dev, prod）: "prod"

    Returns:
        环境标识值: "pre" 或 "prod"
    """
    env = get_current_env()
    if env == "pre":
        return "pre"
    return "prod"


def get_table_name(base_name: str) -> str:
    """根据环境获取带后缀的表名。

    规则：
    - dev/线下: 原表名（如 bcsfuse_workers）
    - pre/预发: 表名 + "_pre"（如 bcsfuse_workers_pre）
    - prod/生产: 原表名（如 bcsfuse_workers）

    Args:
        base_name: 表名基础部分，如 "bcsfuse_workers"

    Returns:
        根据环境处理后的完整表名
    """
    env = get_current_env()
    if env == "pre":
        return f"{base_name}_pre"
    return base_name


@lru_cache(maxsize=1)
def get_server_ip() -> str:
    """获取服务器 IP 地址（缓存）。

    优先从环境变量 SERVER_IP 获取，否则自动获取本机 IP。
    使用 lru_cache 缓存，服务器启动后只计算一次。

    Returns:
        str: 服务器 IP 地址
    """
    # 优先从环境变量获取
    server_ip = os.getenv("SERVER_IP", "")
    if server_ip:
        return server_ip

    # 自动获取本机 IP
    try:
        # 创建 UDP socket 连接到外部地址（不会真正发包）
        # 用于获取本机出口 IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # 使用公共 DNS 服务器地址
            s.connect(("8.8.8.8", 80))
            server_ip = s.getsockname()[0]
        return server_ip
    except Exception:
        # 回退到 hostname 解析
        try:
            hostname = socket.gethostname()
            server_ip = socket.gethostbyname(hostname)
            return server_ip
        except Exception:
            return "unknown"
