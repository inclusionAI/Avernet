#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JSON文件安全更新工具
用于各skill安全地更新JSON文件，支持单字段更新和文件合并

使用方法：
    # 更新单个字段（使用 activity_id 生成文件名）
    python json_updater.py <key> <value> --activity-id <id>

    # 更新单个字段（指定文件路径）
    python json_updater.py <key> <value> [--file <json_file_path>]

    # 合并两个JSON文件
    python json_updater.py --merge --source <source.json> --target <target.json>

示例：
    python json_updater.py "活动类型" "日常活动" --activity-id 300005
    python json_updater.py "是否有风险" "是" --file ../references/final_res_300005.json
    python json_updater.py --merge --source ../references/final_res_300005.json --target output.json
"""

import json
import sys
import os
from typing import Any, Dict, Union

from path_resolver import get_running_data_dir

# 获取 references 目录路径（使用 abspath 规范化，避免 '..' 导致路径不一致）
REFERENCES_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references'))
RUNNING_DATA_DIR = get_running_data_dir()


def get_json_path(activity_id: str = None, file_path: str = None) -> str:
    """
    获取 JSON 文件路径

    优先级：
    1. 如果指定了 file_path，直接使用
    2. 如果指定了 activity_id，使用 final_res_{activity_id}.json（推荐）
    3. 否则使用默认文件名 final_res.json（不推荐，多活动并行时会数据重叠）

    Args:
        activity_id: 活动ID
        file_path: 指定的文件路径

    Returns:
        str: JSON文件路径
    """
    if file_path:
        return file_path
    if activity_id:
        return os.path.join(REFERENCES_DIR, f'final_res_{activity_id}.json')
    # 回退到默认文件名，输出警告
    print("[WARNING] 未指定 activity_id，使用默认文件名 final_res.json（多活动并行时可能数据重叠）")
    return os.path.join(REFERENCES_DIR, 'final_res.json')


def clean_text(text: str) -> str:
    """清理文本中的引号字符，避免JSON解析错误"""
    if not text:
        return text
    # 将中文引号替换为书名号
    text = text.replace('"', '【').replace('"', '】')
    # 将英文双引号替换为单引号（仅在文本内容中，不影响JSON结构）
    text = text.replace('"', "'")
    return text


def read_json(file_path: str) -> Dict[str, Any]:
    """读取JSON文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def write_json(data: Dict[str, Any], file_path: str) -> None:
    """写入JSON文件"""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_json(key: str, value: Any, file_path: str = None) -> Dict[str, Any]:
    """
    更新JSON文件中的单个字段

    Args:
        key: 字段名
        value: 字段值（可以是字符串、列表、字典等）
        file_path: JSON文件路径

    Returns:
        更新后的完整数据
    """
    if file_path is None:
        file_path = get_json_path()
    data = read_json(file_path)

    # 如果值是字符串，进行清理
    if isinstance(value, str):
        value = clean_text(value)

    data[key] = value
    write_json(data, file_path)
    return data


def update_multiple_fields(updates: Dict[str, Any], file_path: str = None) -> Dict[str, Any]:
    """
    批量更新JSON文件中的多个字段

    Args:
        updates: 字段名和值的字典
        file_path: JSON文件路径

    Returns:
        更新后的完整数据
    """
    if file_path is None:
        file_path = get_json_path()
    data = read_json(file_path)

    for key, value in updates.items():
        # 如果值是字符串，进行清理
        if isinstance(value, str):
            value = clean_text(value)
        data[key] = value

    write_json(data, file_path)
    return data


def merge_json_files(source_path: str, target_path: str) -> Dict[str, Any]:
    """
    合并两个JSON文件，将源文件的字段合并到目标文件

    Args:
        source_path: 源JSON文件路径
        target_path: 目标JSON文件路径

    Returns:
        合并后的数据
    """
    # 读取源文件
    source_data = read_json(source_path)

    # 读取目标文件（如果存在）
    if os.path.exists(target_path):
        target_data = read_json(target_path)
    else:
        target_data = {}

    # 合并数据
    for key, value in source_data.items():
        if isinstance(value, str):
            value = clean_text(value)
        target_data[key] = value

    # 写入目标文件
    write_json(target_data, target_path)
    return target_data


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description='安全更新JSON文件')

    # 互斥组：要么更新字段，要么合并文件
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('key', nargs='?', help='要更新的字段名')
    group.add_argument('--merge', '-m', action='store_true', help='合并模式')

    parser.add_argument('value', nargs='?', help='字段值（JSON字符串）')
    parser.add_argument('--file', '-f', help='JSON文件路径')
    parser.add_argument('--activity-id', '-a', help='活动ID，用于生成文件名 final_res_{id}.json')
    parser.add_argument('--source', '-s', help='源JSON文件路径（合并模式）')
    parser.add_argument('--target', '-t', help='目标JSON文件路径（合并模式）')

    args = parser.parse_args()

    try:
        if args.merge:
            # 合并模式
            if not args.source:
                # --activity-id 可用于推导 source
                if args.activity_id:
                    args.source = os.path.join(REFERENCES_DIR, f'final_res_{args.activity_id}.json')
                else:
                    print("错误: 合并模式需要指定 --source 或 --activity-id", file=sys.stderr)
                    sys.exit(1)
            if not args.target:
                # --activity-id 可用于推导 target（RUNNING_DATA 下的分析结果文件）
                if args.activity_id:
                    args.target = os.path.join(RUNNING_DATA_DIR, f'营销活动_{args.activity_id}_分析结果.txt')
                else:
                    print("错误: 合并模式需要指定 --target 或 --activity-id", file=sys.stderr)
                    sys.exit(1)

            result = merge_json_files(args.source, args.target)
            print(f"成功合并文件: {args.source} -> {args.target}", file=sys.stderr)
        else:
            # 更新字段模式
            if args.key is None or args.value is None:
                print("错误: 更新模式需要指定 key 和 value", file=sys.stderr)
                sys.exit(1)

            # 获取文件路径
            json_path = get_json_path(args.activity_id, args.file)

            # 尝试解析value为JSON
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError:
                # 如果解析失败，作为普通字符串处理
                value = args.value

            result = update_json(args.key, value, json_path)
            print(f"成功更新字段 '{args.key}' -> {json_path}", file=sys.stderr)

        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()