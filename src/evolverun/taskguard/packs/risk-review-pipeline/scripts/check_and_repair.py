#!/usr/bin/env python3
"""
完整性检测与自动修复脚本

在保存分析结果前，自动检测必需字段是否完整，并尝试自动修复。
这是方案三的核心实现，旨在解决评审流程中断导致的数据不完整问题。

用法:
    python3 check_and_repair.py --activity-id 1400064
    python3 check_and_repair.py --activity-id 1400064 --auto-repair
    python3 check_and_repair.py --file "营销活动_1400064_分析结果.txt" --auto-repair

输出格式:
    {
        "success": true/false,
        "activity_id": "1400064",
        "is_complete": true/false,
        "repaired": true/false,
        "repaired_fields": [...],
        "message": "..."
    }
"""

import json
import os
import sys
import argparse
import subprocess

# 导入修复模块
from repair_result import find_result_file, smart_repair, validate_result, is_test_activity, parse_filename

# 必需字段定义（与 to_final_res() 输出对齐，基于「数金一页纸内容」文档）
REQUIRED_FIELDS = [
    '活动名称',
    '活动CP号',
    '活动类型',
    '获取限制',
    '是否限制实名',
    '是否限制四同',
    '活动配置是否合理',
    '是否有风险',
    '风险判断原因',
]


def check_completeness(data: dict) -> dict:
    """
    检查数据完整性

    Returns:
        dict: {
            "is_complete": bool,
            "missing_fields": list,
            "present_count": int,
            "total_count": int
        }
    """
    missing = []
    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if value is None or value == '' or (isinstance(value, str) and value.strip() == ''):
            missing.append(field)

    return {
        "is_complete": len(missing) == 0,
        "missing_fields": missing,
        "present_count": len(REQUIRED_FIELDS) - len(missing),
        "total_count": len(REQUIRED_FIELDS)
    }


def check_and_repair(activity_id: str, auto_repair: bool = True) -> dict:
    """
    检查完整性并自动修复

    Args:
        activity_id: 活动ID
        auto_repair: 是否自动修复

    Returns:
        dict: 检查和修复结果
    """
    # 查找结果文件
    result_file = find_result_file(activity_id)
    if not result_file:
        return {
            "success": False,
            "error": f"找不到活动 {activity_id} 的分析结果文件",
            "activity_id": activity_id,
            "is_complete": False
        }

    # 读取数据
    try:
        with open(result_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "error": f"JSON解析失败: {e}",
            "activity_id": activity_id,
            "file": result_file,
            "is_complete": False
        }

    # 检查完整性
    check_result = check_completeness(data)

    result = {
        "success": True,
        "activity_id": activity_id,
        "file": result_file,
        "is_complete": check_result["is_complete"],
        "present_count": check_result["present_count"],
        "total_count": check_result["total_count"],
        "missing_fields": check_result["missing_fields"],
        "repaired": False,
        "repaired_fields": []
    }

    # 如果完整，直接返回
    if check_result["is_complete"]:
        result["message"] = f"完整性检测通过，所有 {check_result['total_count']} 个必需字段已存在"
        return result

    # 不完整，尝试修复
    if auto_repair:
        # 执行修复
        repair_result = smart_repair(data, activity_id)
        repaired_data = repair_result["repaired_data"]

        # 验证修复后
        after_check = check_completeness(repaired_data)

        # 保存修复后的数据
        try:
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(repaired_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {
                "success": False,
                "error": f"写入修复数据失败: {e}",
                "activity_id": activity_id,
                "file": result_file
            }

        result["repaired"] = True
        result["repaired_fields"] = repair_result["repaired_fields"]
        result["repair_details"] = repair_result["repair_details"]
        result["is_test_activity"] = repair_result["is_test_activity"]
        result["is_complete"] = after_check["is_complete"]
        result["missing_fields"] = after_check["missing_fields"]
        result["present_count"] = after_check["present_count"]
        result["message"] = f"已自动修复 {len(repair_result['repaired_fields'])} 个缺失字段"

    else:
        result["message"] = f"发现 {len(check_result['missing_fields'])} 个缺失字段，建议使用 --auto-repair 自动修复"

    return result


def main():
    parser = argparse.ArgumentParser(
        description='完整性检测与自动修复脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    # 仅检测
    python3 check_and_repair.py --activity-id 1400064

    # 检测并自动修复
    python3 check_and_repair.py --activity-id 1400064 --auto-repair

    # 从文件路径检测
    python3 check_and_repair.py --file "营销活动_1400064_分析结果.txt" --auto-repair
        '''
    )
    parser.add_argument('--activity-id', '-a', type=str, help='活动ID')
    parser.add_argument('--file', '-f', type=str, help='直接指定结果文件路径')
    parser.add_argument('--auto-repair', '-r', action='store_true', default=True,
                        help='自动修复缺失字段（默认开启）')
    parser.add_argument('--no-repair', action='store_true', help='仅检测，不自动修复')
    parser.add_argument('--json', '-j', action='store_true', help='仅输出JSON格式')

    args = parser.parse_args()

    # --no-repair 覆盖 --auto-repair
    auto_repair = args.auto_repair and not args.no_repair

    # 确定活动ID
    activity_id = None

    if args.activity_id:
        activity_id = args.activity_id
    elif args.file:
        parsed = parse_filename(args.file)
        if parsed['valid']:
            activity_id = parsed['activity_id']
        else:
            print(json.dumps({
                "success": False,
                "error": f"无法从文件名解析活动ID: {args.file}"
            }, ensure_ascii=False))
            sys.exit(2)
    else:
        print(json.dumps({
            "success": False,
            "error": "需要指定 --activity-id 或 --file 参数"
        }, ensure_ascii=False))
        sys.exit(2)

    # 执行检测和修复
    result = check_and_repair(activity_id, auto_repair)

    # 输出结果
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not args.json and result.get('success'):
        print("\n" + "=" * 60, file=sys.stderr)
        print("📊 完整性检测报告", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        if result.get('repaired'):
            print(f"\n✅ 已自动修复 {len(result['repaired_fields'])} 个字段:", file=sys.stderr)
            for field in result['repaired_fields']:
                detail = result.get('repair_details', {}).get(field, '')
                print(f"   • {field}: {detail}", file=sys.stderr)

        if result.get('is_complete'):
            print(f"\n✅ 完整性检测通过 ({result['present_count']}/{result['total_count']})", file=sys.stderr)
        else:
            print(f"\n⚠️ 仍有缺失字段: {', '.join(result.get('missing_fields', []))}", file=sys.stderr)

        print("\n" + "=" * 60, file=sys.stderr)

    sys.exit(0 if result.get('success') and result.get('is_complete') else 1)


if __name__ == '__main__':
    main()