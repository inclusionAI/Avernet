#!/usr/bin/env python3
"""
从 bot_info.json 读取 device_id，批量执行 openclaw extraDirs 更新脚本
"""

import argparse
import base64
import json
import sys

from bot_info_download import load_device_ids
from agentclaw_api_util import exec_shell


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="批量更新 openclaw extraDirs 配置")
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
PY_SCRIPT = '''#!/usr/bin/env python3
import json, shutil, os, sys
from datetime import datetime

TARGET_FILE = '/home/admin/.openclaw/openclaw.json'
DEFAULT_EXTRA_DIRS = [
    "/home/admin/.openclaw/skills",
    "/home/admin/.extra-skills",
    "/home/admin/nfs/skills-default"
]

def setup_skills_load(data):
    if 'skills' not in data: data['skills'] = {}
    if 'load' not in data['skills']: data['skills']['load'] = {}
    if 'extraDirs' not in data['skills']['load']: data['skills']['load']['extraDirs'] = []
    return data

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
    data = setup_skills_load(data)
    current = data['skills']['load'].get('extraDirs', [])
    if isinstance(current, list) and len(current) < 3:
        if do_backup:
            backup_file = TARGET_FILE + f'.bak.{datetime.now().strftime("%Y%m%d%H%M%S")}'
            shutil.copy(TARGET_FILE, backup_file)
            print(f"[BACKUP] {backup_file}")
        data['skills']['load']['extraDirs'] = DEFAULT_EXTRA_DIRS
        with open(TARGET_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\\n')
        print("[DONE] extraDirs 已更新")
    else:
        print(f"[SKIP] extraDirs 当前有 {len(current)} 个条目，无需修改")

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
        print("[ERROR] 没有找到任何 device_id")
        sys.exit(1)

    print(f"开始批量执行...")

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
