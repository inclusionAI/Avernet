#!/usr/bin/env python3
"""
E2E 验证 /api/mcp/market/detail 的 live fetch 降级逻辑。

用法:
    cd src/backend
    uv run python tests/shell/verify_mcp_market_detail.py --server-code mcp.xxx

如果需要验证 live fetch（带 IAM_TOKEN cookie）:
    uv run python tests/shell/verify_mcp_market_detail.py \
        --server-code mcp.xxx \
        --iam-token "eyJraWQiOiJkZWZhdWx0..."

脚本会先后请求两次：
1. 不带 cookie —— 走 MCP Center 缓存
2. 带 IAM_TOKEN cookie —— 尝试直连 MCP Server 拉取最新 tools

如果 live fetch 成功，两次返回的 tools 列表会有差异。
"""

import argparse
import json
import sys

import requests

BASE_URL = "http://agentclaw-local.stable.teamclaw.net:8888"
ENDPOINT = "/api/mcp/market/detail"


def fetch_detail(server_code: str, iam_token: str | None = None) -> dict:
    """请求 backend 的 market/detail 接口。"""
    url = f"{BASE_URL}{ENDPOINT}"
    params = {"server_code": server_code}
    cookies = {}
    if iam_token:
        cookies["IAM_TOKEN"] = iam_token

    try:
        resp = requests.get(url, params=params, cookies=cookies, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as e:
        print(f"[ERROR] 无法连接到 backend ({BASE_URL})，请确认服务已启动: {e}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("[ERROR] 请求超时")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] HTTP 错误: {e}")
        try:
            print(f"  响应: {resp.text}")
        except Exception:
            pass
        sys.exit(1)


def extract_tools(data: dict) -> list[dict]:
    """从响应中提取 tools 列表。"""
    if not data.get("success"):
        return []
    return data.get("data", {}).get("tools", []) or []


def print_tools(tools: list[dict], max_display: int = 10):
    """格式化打印 tools 列表。"""
    if not tools:
        print("  (无 tools)")
        return
    print(f"  共 {len(tools)} 个 tool(s):")
    for i, tool in enumerate(tools[:max_display], 1):
        name = tool.get("name", "N/A")
        desc = tool.get("description", "")
        print(f"    {i}. {name}" + (f" — {desc[:50]}" if desc else ""))
    if len(tools) > max_display:
        print(f"    ... 还有 {len(tools) - max_display} 个")


def compare_tools(cached_tools: list[dict], live_tools: list[dict]) -> None:
    """对比缓存 tools 和 live tools 的差异。"""
    cached_names = {t.get("name") for t in cached_tools if t.get("name")}
    live_names = {t.get("name") for t in live_tools if t.get("name")}

    only_in_live = live_names - cached_names
    only_in_cache = cached_names - live_names

    print("\n" + "=" * 50)
    print("【diff 结果】")
    if only_in_live:
        print(f"  live fetch 多出 {len(only_in_live)} 个 tool: {sorted(only_in_live)}")
    if only_in_cache:
        print(f"  缓存里多 {len(only_in_cache)} 个 tool (live 已删除): {sorted(only_in_cache)}")
    if not only_in_live and not only_in_cache:
        print("  tools 列表完全一致（可能 live fetch 失败，降级到缓存数据）")


def main():
    parser = argparse.ArgumentParser(description="E2E 验证 MCP market/detail")
    parser.add_argument(
        "--server-code", "-s",
        type=str,
        required=True,
        help="MCP server code，如 mcp.test"
    )
    parser.add_argument(
        "--iam-token", "-t",
        type=str,
        default=None,
        help="浏览器 Cookie 里的 IAM_TOKEN 值（完整 JWT 字符串）"
    )
    parser.add_argument(
        "--full-response", "-f",
        action="store_true",
        help="打印完整响应 JSON"
    )
    args = parser.parse_args()

    print(f"目标 MCP: {args.server_code}")
    print(f"backend:  {BASE_URL}")
    print("=" * 50)

    # 1) 不带 cookie —— 纯缓存
    print("\n[1/2] 不带 IAM_TOKEN（MCP Center 缓存数据）")
    cache_resp = fetch_detail(args.server_code)
    if not cache_resp.get("success"):
        print(f"[ERROR] 请求失败: {cache_resp}")
        sys.exit(1)

    cached_tools = extract_tools(cache_resp)
    print_tools(cached_tools)
    if args.full_response:
        print("  完整响应:")
        print(json.dumps(cache_resp, indent=2, ensure_ascii=False))

    # 2) 如果给了 token，再带 cookie 请求一次
    if args.iam_token:
        print("\n[2/2] 带 IAM_TOKEN（尝试 live fetch）")
        live_resp = fetch_detail(args.server_code, iam_token=args.iam_token)
        if not live_resp.get("success"):
            print(f"[ERROR] 请求失败: {live_resp}")
            sys.exit(1)

        live_tools = extract_tools(live_resp)
        print_tools(live_tools)
        if args.full_response:
            print("  完整响应:")
            print(json.dumps(live_resp, indent=2, ensure_ascii=False))

        compare_tools(cached_tools, live_tools)
    else:
        print("\n[提示] 未提供 --iam-token，跳过 live fetch 对比")

    print("\n验证完成。")


if __name__ == "__main__":
    main()
