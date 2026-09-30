#!/usr/bin/env python3
"""
AgentClaw API 工具模块
提供设备连接和 shell 命令执行的公共方法
"""

import http.client
import json
from typing import Any, Tuple, Optional

try:
    import requests
except ImportError:
    requests = None

# Cookie 配置 — never hardcode a real auth cookie here (it embeds live session
# tokens + identity). Provide it at runtime via set_cookie() or the
# AGENTCLAW_API_COOKIE env var.
import os

COOKIE = os.getenv("AGENTCLAW_API_COOKIE", "")


def set_cookie(cookie: str) -> None:
    """设置 Cookie

    Args:
        cookie: 鉴权 Cookie
    """
    global COOKIE
    COOKIE = cookie


def _build_host(env: str) -> str:
    """根据环境构建目标主机名

    Args:
        env: 环境标识 (pre/prod/gray/dev)

    Returns:
        目标主机名
    """
    return f"agentclaw-{env}.teamclaw.com"


def fetch_connectable_devices(
    env: str,
    page: int = 1,
    page_size: int = 100,
    with_connection: bool = False,
    port: Optional[int] = None,
) -> Optional[dict]:
    """调用 list_connectable_devices_admin 接口获取可连接设备列表。

    Args:
        env: 环境标识 (pre/prod/gray/dev)
        page: 页码 (默认：1)
        page_size: 每页数量 (默认：100)
        with_connection: 是否包含连接信息 (默认：False)
        port: 连接端口 (可选)

    Returns:
        API 响应数据，失败则返回 None
    """
    if requests is None:
        return None

    host = _build_host(env)
    url = f"https://{host}/api/v1/devices/connectable_admin"

    params = {
        "page": page,
        "page_size": page_size,
    }

    if with_connection:
        params["with_connection"] = "true"
    if port is not None:
        params["port"] = port

    try:
        response = requests.get(url, params=params, headers={"Cookie": COOKIE}, timeout=30)
        response.raise_for_status()

        result = response.json()
        return result if result.get("success") else None

    except Exception:
        return None


def exec_shell(
    env: str,
    client_id: str,
    shell_cmd: str
) -> Tuple[bool, Any]:
    """发送单个请求到设备执行 shell 命令 (使用 su admin)

    Args:
        env: 环境标识 (pre/prod/gray/dev)
        client_id: 设备 ID
        shell_cmd: 要执行的 shell 命令

    Returns:
        (success, result) 元组:
        - success: 请求是否成功
        - result: 成功时返回 API 响应，失败时返回错误信息
    """
    try:
        host = _build_host(env)
        conn = http.client.HTTPSConnection(host)
        payload = json.dumps({
            "client_ids": [client_id],
            "shell_cmd": f"su admin -c '{shell_cmd}'"
        })
        headers = {
            'Cookie': COOKIE,
            'Content-Type': 'application/json'
        }
        conn.request("POST", "/api/v1/devices/exec_shell", payload, headers)
        res = conn.getresponse()
        data = res.read()
        result = json.loads(data.decode("utf-8"))
        conn.close()
        return True, result
    except Exception as e:
        return False, str(e)


def exec_shell_simple(
    env: str,
    client_id: str,
    shell_cmd: str
) -> Tuple[bool, Any]:
    """发送单个请求到设备执行 shell 命令（不使用 su admin）

    Args:
        env: 环境标识 (pre/prod/gray/dev)
        client_id: 设备 ID
        shell_cmd: 要执行的 shell 命令

    Returns:
        (success, result) 元组:
        - success: 请求是否成功
        - result: 成功时返回 API 响应，失败时返回错误信息
    """
    try:
        host = _build_host(env)
        conn = http.client.HTTPSConnection(host)
        payload = json.dumps({
            "client_ids": [client_id],
            "shell_cmd": shell_cmd
        })
        headers = {
            'Cookie': COOKIE,
            'Content-Type': 'application/json'
        }
        conn.request("POST", "/api/v1/devices/exec_shell", payload, headers)
        res = conn.getresponse()
        data = res.read()
        result = json.loads(data.decode("utf-8"))
        conn.close()
        return True, result
    except Exception as e:
        return False, str(e)
