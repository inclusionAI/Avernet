#!/usr/bin/env python3
"""
从 bot_info.json 读取 device_id，批量执行健康检查
"""

import argparse
import json
import sys

from bot_info_download import load_device_ids
from agentclaw_api_util import exec_shell_simple

CMD = 'curl 127.0.0.1:20003/health'


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="批量执行健康检查")
    parser.add_argument(
        "--env", "-e",
        type=str,
        default='pre',
        help="环境标识 (默认：pre)"
    )
    return parser.parse_args()


def _check_health_status(result):
    """
    检查健康检查结果是否成功
    只有返回 "status":"ok" 才算健康检查成功

    返回结果格式:
    {
        'success': True,
        'data': {
            'results': [{
                'client_id': '...',
                'status': 'success',
                'result': 'CommandResult(stdout=\'{"status":"ok","engine":"openclaw"}\', ...)'
            }]
        }
    }
    """
    if not result:
        return False

    data = result.get('data', {}).get('results', [])
    if not data:
        return False

    res_raw = data[0]

    # 如果是 dict，检查 result 字段
    if isinstance(res_raw, dict):
        result_str = res_raw.get('result', '')

        # 从 CommandResult 字符串中提取 stdout
        # CommandResult(stdout='{"status":"ok","engine":"openclaw"}', stderr='...', ...)
        if 'stdout=' in result_str:
            # 提取 stdout 内容
            import re
            match = re.search(r"stdout='([^']*)'", result_str)
            if match:
                stdout = match.group(1)
                try:
                    stdout_obj = json.loads(stdout)
                    return stdout_obj.get('status') == 'ok'
                except (json.JSONDecodeError, ValueError):
                    return False

    return False


def main():
    args = parse_args()

    # 加载 device_ids
    device_ids = load_device_ids(args.env)

    if not device_ids:
        print("[ERROR] 没有找到任何 device_id")
        sys.exit(1)

    print(f"共加载 {len(device_ids)} 个 device_id")
    print(f"开始批量执行健康检查...")
    print(f"命令：{CMD}")
    print("=" * 50)

    successful = 0
    failed = 0
    failed_list = []

    for i, device_id in enumerate(device_ids, 1):
        print(f"[{i}/{len(device_ids)}] 正在处理：{device_id}")

        success, result = exec_shell_simple(args.env, device_id, CMD)

        # 检查健康检查是否真正成功 (status=="ok")
        health_ok = _check_health_status(result)

        if success and result.get('success') and health_ok:
            successful += 1
            print(f"  -> 健康检查通过")
        else:
            failed += 1
            failed_list.append({'device_id': device_id, 'error': result})
            print(f"  -> 健康检查失败：{result}")

    # 打印汇总
    print("\n" + "=" * 50)
    print("执行汇总:")
    print(f"  总数：{len(device_ids)}")
    print(f"  成功：{successful}")
    print(f"  失败：{failed}")

    if failed_list:
        print("\n失败列表 (前 10 个):")
        for item in failed_list[:10]:
            print(f"  - {item['device_id']}: {item['error']}")
        if len(failed_list) > 10:
            print(f"  ... 还有 {len(failed_list) - 10} 个失败项")


if __name__ == '__main__':
    main()
