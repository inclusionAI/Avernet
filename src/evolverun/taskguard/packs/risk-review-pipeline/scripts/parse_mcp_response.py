#!/usr/bin/env python3
"""
MCP响应解析工具 - 通用解析脚本

读取MCP响应文本，解析出数据项列表

用法:
    echo '{"success":true,"data":{...}}' | python3 parse_mcp_response.py
    python3 parse_mcp_response.py < mcp_response.json
"""

import sys
import json
import os
from typing import Tuple, Optional, List, Dict


# ==============================================================================
# MCP 响应解析函数
# ==============================================================================

def parse_mcp_response(response_text: str) -> List[Dict]:
    """
    解析MCP查询响应，提取数据项列表

    Args:
        response_text: MCP原始响应文本

    Returns:
        list: 数据项列表

    Raises:
        ValueError: 解析失败时抛出
    """
    response = json.loads(response_text)

    if not response.get('success'):
        raise ValueError(f"MCP响应失败: {response.get('error', 'Unknown error')}")

    # 提取嵌套的data
    outer_data = response.get('data', {})
    inner_data_str = outer_data.get('data', '[]')

    if isinstance(inner_data_str, str):
        inner_data = json.loads(inner_data_str)
    else:
        inner_data = inner_data_str

    # 检查内部success
    if inner_data.get('success') is False:
        raise ValueError(f"MCP内部调用失败: {inner_data.get('errorMsg', 'Unknown error')}")

    # 获取数据列表
    items = inner_data.get('data', [])
    if isinstance(items, str):
        items = json.loads(items)

    return items if isinstance(items, list) else []


def parse_save_response(response_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    解析MCP写入响应，提取record_id

    Args:
        response_text: MCP原始响应文本

    Returns:
        tuple: (record_id, error_message)
    """
    try:
        response = json.loads(response_text)

        if not response.get('success'):
            return None, f"MCP响应失败: {response.get('error', 'Unknown error')}"

        # 提取嵌套的data
        outer_data = response.get('data', {})
        inner_data_str = outer_data.get('data', '{}')

        if isinstance(inner_data_str, str):
            inner_data = json.loads(inner_data_str)
        else:
            inner_data = inner_data_str

        # 检查内部success
        if inner_data.get('success') is False:
            return None, f"MCP内部调用失败: {inner_data.get('errorMsg', 'Unknown error')}"

        # 获取record_id
        record_id = inner_data.get('data')
        if record_id:
            return str(record_id), None

        return None, "未获取到record_id"

    except json.JSONDecodeError as e:
        return None, f"JSON解析失败: {e}"
    except Exception as e:
        return None, f"解析响应失败: {e}"


def main():
    # 从stdin读取MCP响应
    if sys.stdin.isatty():
        print(json.dumps({
            "success": False,
            "error": "请通过stdin传入MCP响应文本"
        }, ensure_ascii=False))
        sys.exit(1)

    try:
        response_text = sys.stdin.read().strip()

        # 判断是查询响应还是写入响应
        response = json.loads(response_text)

        if 'data' in response.get('data', {}):
            inner_str = response.get('data', {}).get('data', '{}')
            if isinstance(inner_str, str):
                inner = json.loads(inner_str)
                # 如果有data列表，是查询响应
                if isinstance(inner.get('data'), list):
                    result = parse_mcp_response(response_text)
                    print(json.dumps({
                        "success": True,
                        "data": result,
                        "count": len(result)
                    }, ensure_ascii=False))
                else:
                    # 是写入响应
                    record_id, error = parse_save_response(response_text)
                    if error:
                        print(json.dumps({
                            "success": False,
                            "error": error
                        }, ensure_ascii=False))
                    else:
                        print(json.dumps({
                            "success": True,
                            "record_id": record_id
                        }, ensure_ascii=False))
        else:
            print(json.dumps({
                "success": False,
                "error": "无法识别的响应格式"
            }, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False))


if __name__ == '__main__':
    main()