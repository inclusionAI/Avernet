#!/usr/bin/env python3
"""
子 Agent 共享工具模块
提供 MCP 调用、配置加载等公共方法（无 SQLite）。

用法：
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'config'))
from mcp_utils import load_config, run_mcp_call
"""

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

DEFAULTS = {
    "retry_count": 3,
    "retry_delay": 2,
    "timeout": 30,
    "max_supplement_rounds": 2,
}


def load_config(config_path: str) -> Dict[str, Any]:
    """加载配置文件，合并默认值"""
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ 配置文件格式错误: {e}", file=sys.stderr)
        sys.exit(1)

    for key, value in DEFAULTS.items():
        if key not in config:
            config[key] = value

    return config


def run_mcp_call(server: str, tool: str, args: dict, config: Dict[str, Any]) -> Optional[dict]:
    """
    调用 MCP，支持重试机制

    Args:
        server: MCP 服务名
        tool: MCP 工具名
        args: 调用参数
        config: 配置字典（含 retry_count, retry_delay, timeout）

    Returns:
        成功返回 response data，失败返回 None
    """
    args_json = json.dumps(args, ensure_ascii=False)
    cmd = [
        "mcporter", "call",
        "--server", server,
        "--tool", tool,
        "--args", args_json,
        "--output", "json"
    ]

    retry_count = config.get("retry_count", 3)
    retry_delay = config.get("retry_delay", 2)
    timeout = config.get("timeout", 30)

    last_error = None
    for attempt in range(1, retry_count + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode == 0:
                try:
                    response = json.loads(result.stdout)
                    if response.get("success") or "data" in response or "result" in response:
                        return response
                    else:
                        trace_id = response.get("traceId", "unknown")
                        last_error = f"MCP 返回异常, traceId={trace_id}"
                except json.JSONDecodeError as e:
                    last_error = f"JSON 解析错误: {e}, stdout={repr(result.stdout[:200])}"
            else:
                last_error = f"MCP 调用失败: {result.stderr[:200]}"

        except subprocess.TimeoutExpired:
            last_error = "MCP 调用超时"
        except Exception as e:
            last_error = f"异常: {e}"

        if attempt < retry_count:
            print(f"第 {attempt} 次尝试失败，{last_error}，{retry_delay}秒后重试...", file=sys.stderr)
            time.sleep(retry_delay)
        else:
            print(f"第 {attempt} 次尝试失败，已用尽重试次数: {last_error}", file=sys.stderr)

    return None