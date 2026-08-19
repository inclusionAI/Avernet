#!/usr/bin/env python3
"""
营销活动流程归档脚本
功能：
1. 检查是否所有活动都已完成评审（通过检查是否有对应的分析结果文件）
2. 所有完成后，将所有txt文件重命名为 已完成_前缀
3. 删除其他格式文件
"""

import os
import sys
import json
import glob
import fcntl
import re
import argparse

# 导入统一路径检测
from path_resolver import get_running_data_dir

def acquire_lock(lock_file):
    """获取文件锁，防止并发归档"""
    fd = open(lock_file, 'w')
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except IOError:
        fd.close()
        return None

def get_activity_files():
    """获取所有营销活动相关文件"""
    running_dir = get_running_data_dir()
    pattern = os.path.join(running_dir, "营销活动_*.txt")
    files = glob.glob(pattern)
    return files

def group_files_by_activity(files):
    """按活动ID分组文件"""
    groups = {}
    for f in files:
        basename = os.path.basename(f)
        # 跳过已完成的文件
        if basename.startswith("已完成_"):
            continue
        # 提取活动ID，格式：营销活动_{id}_{类型}.txt
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

def archive_files(activity_id=None):
    """执行归档操作

    Args:
        activity_id: 指定活动ID时只归档该活动的文件，为None时归档所有已完成活动
    """
    running_dir = get_running_data_dir()

    # 获取所有相关文件
    all_files = get_activity_files()

    if not all_files:
        return {"success": True, "message": "没有需要归档的文件", "archived": []}

    # 按活动分组
    groups = group_files_by_activity(all_files)

    # 指定活动ID模式：只处理该活动
    if activity_id:
        if activity_id not in groups:
            return {"success": True, "message": f"活动 {activity_id} 没有需要归档的文件", "archived": []}
        groups = {activity_id: groups[activity_id]}

    archived_files = []
    deleted_files = []
    skipped_groups = []

    for aid, files in groups.items():
        # 检查该活动是否已完成
        if not is_activity_completed(files):
            skipped_groups.append(activity_id)
            continue

        # 归档该活动的所有txt文件
        for f in files:
            basename = os.path.basename(f)
            if basename.startswith("已完成_"):
                continue

            new_name = "已完成_" + basename
            new_path = os.path.join(running_dir, new_name)

            try:
                os.rename(f, new_path)
                archived_files.append({
                    "old": basename,
                    "new": new_name
                })
            except Exception as e:
                return {"success": False, "error": f"归档失败 {basename}: {str(e)}"}

    # 删除其他格式文件（非txt且非已完成_开头）
    other_pattern = os.path.join(running_dir, "*")
    for f in glob.glob(other_pattern):
        basename = os.path.basename(f)
        # 跳过目录、txt文件和已完成的文件
        if os.path.isdir(f):
            continue
        if f.endswith('.txt'):
            continue
        if basename.startswith("已完成_"):
            continue

        try:
            os.remove(f)
            deleted_files.append(basename)
        except Exception as e:
            return {"success": False, "error": f"删除失败 {basename}: {str(e)}"}

    return {
        "success": True,
        "message": f"归档完成：{len(archived_files)}个文件已归档，{len(deleted_files)}个文件已删除",
        "archived": archived_files,
        "deleted": deleted_files,
        "skipped_groups": skipped_groups
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="营销活动归档脚本")
    parser.add_argument("--activity-id", "-a", type=str, help="指定活动ID，只归档该活动的文件")
    args = parser.parse_args()

    # 获取文件锁
    lock_path = os.path.join(get_running_data_dir(), ".archive.lock")
    lock_fd = acquire_lock(lock_path)

    if lock_fd is None:
        # 另一个归档正在进行，跳过
        print(json.dumps({"success": True, "message": "另一个归档正在进行，跳过"}))
        sys.exit(0)

    try:
        result = archive_files(activity_id=args.activity_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        try:
            os.remove(lock_path)
        except:
            pass
