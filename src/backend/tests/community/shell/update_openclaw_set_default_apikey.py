#!/usr/bin/env python3
"""
从 bot_info.json 读取 device_id，批量执行 openclaw 配置更新脚本
设置 models.providers 和 agents.defaults 配置
"""

import argparse
import json
import sys
import base64

from bot_info_download import load_device_ids
from agentclaw_api_util import exec_shell


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="批量更新 openclaw 默认配置")
    parser.add_argument(
        "--env", "-e",
        type=str,
        default='pre',
        help="环境标识 (默认：pre)"
    )
    parser.add_argument(
        "--json-file",
        type=str,
        default=None,
        help=f"bot_info.json 文件路径 (默认：bot_info_<env>.json)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印，不实际执行"
    )
    return parser.parse_args()


# 远端执行的 Python 脚本（base64 编码避免转义问题）
# 注意：${API-KEY} 作为字符串字面量，不是 shell 变量
PY_SCRIPT = r'''#!/usr/bin/env python3
import json, shutil, os, sys
from datetime import datetime

TARGET_FILE = '/home/admin/.openclaw/openclaw.json'

def main():
    do_backup = '--backup' in sys.argv
    if not os.path.exists(TARGET_FILE):
        print(f"[ERROR] 文件不存在：{TARGET_FILE}")
        sys.exit(1)

    try:
        with open(TARGET_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            data = json.loads(content) if content.strip() else {}
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 解析错误：{e}")
        sys.exit(1)

    # 创建备份
    if do_backup:
        backup_file = TARGET_FILE + f'.bak.{datetime.now().strftime("%Y%m%d%H%M%S")}'
        shutil.copy(TARGET_FILE, backup_file)
        print(f"[BACKUP] {backup_file}")

    # 更新 models 配置 (providers + 可用模型列表)
    data['models'] = {
        "mode": "merge",
        "providers": {
            "antchat": {
                "baseUrl": "https://antchat.teamclaw.com/v1",
                "apiKey": "${API-KEY}",
                "api": "openai-completions",
                "models": [
                    {
                        "id": "Kimi-K2.5",
                        "name": "Kimi-K2.5",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 200000,
                        "maxTokens": 8192
                    },
                    {
                        "id": "GLM-5",
                        "name": "GLM-5",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 200000,
                        "maxTokens": 8192
                    },
                    {
                        "id": "Qwen3.5-397B-A17B",
                        "name": "Qwen3.5-397B-A17B",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 200000,
                        "maxTokens": 8192
                    },
                    {
                        "id": "lemon_1",
                        "name": "Lemon-1",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 200000,
                        "maxTokens": 8192
                    }
                ]
            }
        }
    }

    # 更新 agents 默认配置
    data['agents'] = {
        "defaults": {
            "model": {
                "primary": "antchat/Kimi-K2.5"
            },
            "models": {
                "antchat/Kimi-K2.5": {},
                "antchat/GLM-5": {},
                "antchat/Qwen3.5-397B-A17B": {},
                "antchat/lemon_1": {}
            },
            "compaction": {
                "mode": "safeguard"
            },
            "maxConcurrent": 4,
            "subagents": {
                "maxConcurrent": 8
            }
        }
    }

    # 写回文件
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')

    print("[DONE] models.providers 和 agents.defaults 已更新，apiKey=${API-KEY}")

if __name__ == '__main__':
    main()
'''

# base64 编码
SCRIPT_B64 = base64.b64encode(PY_SCRIPT.encode()).decode()
# 远端解码并执行
SHELL_CMD = f"echo '{SCRIPT_B64}' | base64 -d > /tmp/update_openclaw_extradir.py && python3 /tmp/update_openclaw_extradir.py --backup"


def main():
    args = parse_args()

    # 加载 device_ids
    device_ids = load_device_ids(args.env)

    if not device_ids:
        print(f"[WARN] 没有找到任何 device_id")
        sys.exit(0)

    print(f"开始批量执行 openclaw 配置更新...")

    successful = 0
    failed = 0
    failed_list = []

    for i, device_id in enumerate(device_ids, 1):
        print(f"[{i}/{len(device_ids)}] 正在处理：{device_id}")
        success, result = exec_shell(args.env, device_id, SHELL_CMD)

        if success and result.get('success'):
            successful += 1
            # 解析结果详情
            data = result.get('data', {}).get('results', [])
            if data:
                res_raw = data[0]
                # 如果是字符串，尝试解析 JSON
                if isinstance(res_raw, str):
                    try:
                        res_obj = json.loads(res_raw)
                        stdout = res_obj.get('stdout', '')
                        if stdout:
                            print(f"  -> DONE: {stdout.strip()}")
                        else:
                            print(f"  -> {res_raw}")
                    except json.JSONDecodeError:
                        print(f"  -> {res_raw}")
                elif isinstance(res_raw, dict):
                    # 可能有一个 'result' 字段包含实际结果
                    res_obj = res_raw.get('result', res_raw)
                    print(f"  -> {res_obj}")
                else:
                    print(f"  -> 未知格式：{res_raw}")
        else:
            failed += 1
            failed_list.append({'device_id': device_id, 'error': result})
            print(f"  -> 失败：{result}")

    # 打印汇总
    print("\n" + "=" * 50)
    print("执行汇总:")
    print(f"  总数：{len(device_ids)}")
    print(f"  成功：{successful}")
    print(f"  失败：{failed}")

    if failed_list:
        print("\n失败列表:")
        for item in failed_list:
            print(f"  - {item['device_id']}: {item['error']}")


if __name__ == '__main__':
    main()
