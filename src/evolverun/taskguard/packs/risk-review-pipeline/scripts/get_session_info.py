#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取活动信息并匹配对应的输入文件

输出包含：
- input_file: 匹配的输入文件路径
- output_file: 输出结果文件路径
- activity_id: 活动ID
- references_file: references目录的最终结果文件路径
"""

import json
import os
import sys

# 导入路径解析器
try:
    from path_resolver import get_running_data_dir
    from mcp_adapter import get_references_dir as _get_refs_dir
except ImportError:
    from mcp_adapter import get_running_data_dir, get_references_dir as _get_refs_dir

RUNNING_DATA_DIR = get_running_data_dir()
REFERENCES_DIR = _get_refs_dir()


def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description='获取活动信息并匹配初始化文件')
    parser.add_argument('--activity-id', '-a', dest='activity_id', required=True,
                        help='活动ID（必需参数）')
    args = parser.parse_args()

    activity_id = args.activity_id
    input_file = os.path.join(RUNNING_DATA_DIR, f"营销活动_{activity_id}_初始化.txt")

    if not os.path.exists(input_file):
        print(json.dumps({
            "error": f"活动ID {activity_id} 对应的初始化文件不存在",
            "search_dir": RUNNING_DATA_DIR
        }, ensure_ascii=False))
        sys.exit(1)

    result = {
        "activity_id": activity_id,
        "input_file": input_file,
        "output_file": os.path.join(RUNNING_DATA_DIR, f"营销活动_{activity_id}_分析结果.txt"),
        "base_name": f"营销活动_{activity_id}_初始化",
        "references_file": os.path.join(REFERENCES_DIR, f'final_res_{activity_id}.json')
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()