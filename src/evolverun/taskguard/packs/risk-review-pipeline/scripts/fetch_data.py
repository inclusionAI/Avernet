#!/usr/bin/env python3
"""
营销活动数据获取脚本 - 使用 mcp_adapter

从Rone获取活动数据，解析后保存为标准格式文件。

用法:
    python3 fetch_data.py --id 300005
    DATA_TYPE=ACTIVITY_DATA python3 fetch_data.py

输出:
    {"success": true, "files": ["营销活动_300005_初始化.txt"], "count": 1}
"""

import json
import os
import sys

from mcp_adapter import (
    fetch_activity_data, get_running_data_dir,
    build_filename, parse_filename, TYPE_TO_CN
)

RUNNING_DATA_DIR = get_running_data_dir()


def fetch_and_save(data_type='ACTIVITY_DATA', activity_id=None):
    """从Rone获取数据并保存为标准格式文件"""
    os.makedirs(RUNNING_DATA_DIR, exist_ok=True)

    try:
        result = fetch_activity_data(data_type, activity_id=activity_id)
        if not result['success']:
            return {"success": False, "error": result['error'], "files": [], "count": 0}

        items = result['items']
        files = []
        for item in items:
            item_id = item.get('id')
            sub_type = item.get('subType', data_type)
            if not item_id:
                continue

            data_type_cn = TYPE_TO_CN.get(sub_type, sub_type)
            filename = build_filename(item_id, data_type_cn)
            file_path = os.path.join(RUNNING_DATA_DIR, filename)

            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(item, f, ensure_ascii=False, indent=2)
                files.append(filename)
            except Exception as e:
                print(f"[Warning] 保存文件失败 {filename}: {e}", file=sys.stderr)

        return {"success": True, "files": files, "count": len(files)}

    except Exception as e:
        return {"success": False, "error": f"执行失败: {str(e)}", "files": [], "count": 0}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="从Rone获取营销活动数据")
    parser.add_argument("--id", dest="activity_id", default=None, help="活动ID")
    args = parser.parse_args()

    data_type = os.environ.get('DATA_TYPE', 'ACTIVITY_DATA')
    # 注意：不再读取 stdin。
    # workflow 的 cli-script executor 使用 stdio=["inherit", "pipe", "pipe"]，
    # stdin 继承 gateway daemon 进程，永远不会发 EOF。
    # sys.stdin.read() 会永久阻塞，导致 60s 超时后被杀。
    # 参数通过 ARG_* 环境变量或 --id 传入，无需从 stdin 读数据。

    result = fetch_and_save(data_type, activity_id=args.activity_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['success'] else 1)


if __name__ == '__main__':
    main()