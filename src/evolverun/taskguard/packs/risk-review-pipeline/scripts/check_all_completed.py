#!/usr/bin/env python3
"""
检查活动是否已完成评审并触发归档
功能：
1. 按活动ID归档：只归档指定活动ID的文件，不检查其他活动
2. 兼容旧逻辑：不传--activity-id时检查所有活动是否完成
"""

import os
import sys
import json
import glob
import re
import argparse

# 导入统一路径检测
from path_resolver import get_running_data_dir

def get_activity_files():
    """获取所有营销活动相关文件（不包括已完成）"""
    running_dir = get_running_data_dir()
    pattern = os.path.join(running_dir, "营销活动_*.txt")
    files = glob.glob(pattern)
    # 过滤掉已完成的文件
    return [f for f in files if not os.path.basename(f).startswith("已完成_")]

def group_files_by_activity(files):
    """按活动ID分组文件"""
    groups = {}
    for f in files:
        basename = os.path.basename(f)
        match = re.search(r'营销活动_(\d+)_', basename)
        if match:
            activity_id = match.group(1)
            if activity_id not in groups:
                groups[activity_id] = []
            groups[activity_id].append(f)
    return groups

def is_activity_completed(activity_files):
    """检查活动是否已完成

    支持多种场景：
    - 初始化场景：需要"初始化" + "分析结果"
    - 重跑场景：需要"用户重跑"(或"重跑") + "分析结果"(或"重跑结果")
    - 确认场景：需要"用户确认通过"(或"用户确认拒绝") + "分析结果"
    """
    has_source = False  # 有数据来源文件
    has_result = False  # 有结果文件

    for f in activity_files:
        basename = os.path.basename(f)

        # 数据来源文件类型
        if any(x in basename for x in ['_初始化', '_用户重跑', '_重跑', '_用户确认通过', '_用户确认拒绝', '_提交ART']):
            has_source = True

        # 结果文件类型
        if any(x in basename for x in ['_分析结果', '_重跑结果']):
            has_result = True

    return has_source and has_result

def _run_archive(activity_id=None):
    """执行归档，直接调用 archive_completed 模块"""
    from archive_completed import archive_files
    return archive_files(activity_id=activity_id)


def check_and_archive(activity_id=None):
    """检查是否完成并触发归档

    Args:
        activity_id: 指定活动ID时只归档该活动的文件，不检查其他活动
    """
    files = get_activity_files()

    if not files:
        return {"all_completed": True, "triggered": True, "message": "没有待处理文件，直接归档"}

    groups = group_files_by_activity(files)

    if not groups:
        return {"all_completed": True, "triggered": True, "message": "没有待处理活动，直接归档"}

    # 指定活动ID模式：只归档该活动，不检查其他活动
    if activity_id:
        if activity_id not in groups:
            return {"all_completed": True, "triggered": False, "message": f"活动 {activity_id} 没有待归档文件"}

        activity_files = groups[activity_id]
        if not is_activity_completed(activity_files):
            return {
                "all_completed": False,
                "triggered": False,
                "message": f"活动 {activity_id} 尚未完成评审",
                "incomplete_activities": [activity_id]
            }

        # 归档指定活动
        try:
            archive_result = _run_archive(activity_id=activity_id)
            return {
                "all_completed": True,
                "triggered": True,
                "message": f"活动 {activity_id} 已完成，已触发归档",
                "archive_result": archive_result
            }
        except Exception as e:
            return {
                "all_completed": True,
                "triggered": False,
                "error": f"归档失败: {str(e)}"
            }

    # 兼容旧逻辑：不指定活动ID时检查所有活动
    all_completed = True
    incomplete_activities = []

    for aid, activity_files in groups.items():
        if not is_activity_completed(activity_files):
            all_completed = False
            incomplete_activities.append(aid)

    if all_completed:
        try:
            archive_result = _run_archive()
            return {
                "all_completed": True,
                "triggered": True,
                "message": "所有活动已完成，已触发归档",
                "archive_result": archive_result
            }
        except Exception as e:
            return {
                "all_completed": True,
                "triggered": False,
                "error": f"归档失败: {str(e)}"
            }
    else:
        return {
            "all_completed": False,
            "triggered": False,
            "message": f"还有 {len(incomplete_activities)} 个活动未完成",
            "incomplete_activities": incomplete_activities
        }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查活动完成状态并归档")
    parser.add_argument("--activity-id", "-a", type=str, help="指定活动ID，只归档该活动的文件")
    args = parser.parse_args()

    result = check_and_archive(activity_id=args.activity_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("all_completed") else 1)