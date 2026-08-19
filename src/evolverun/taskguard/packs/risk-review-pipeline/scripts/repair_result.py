#!/usr/bin/env python3
"""
评审结果自动修复脚本

当评审结果不完整时，自动补全缺失字段。
支持两种修复模式：
1. 智能推断：根据已有信息推断缺失字段
2. 默认值填充：对于无法推断的字段，使用合理的默认值

用法:
    python3 repair_result.py --activity-id 300005
    python3 repair_result.py --file "营销活动_300005_分析结果.txt"
    python3 repair_result.py --activity-id 300005 --dry-run  # 仅预览，不写入

输出格式:
    {
        "success": true/false,
        "activity_id": "300005",
        "repaired_fields": [...],
        "repair_details": {...},
        "message": "..."
    }
"""

import json
import os
import sys
import re
import argparse
import glob

# 导入统一路径检测
from path_resolver import get_running_data_dir
from mcp_adapter import get_references_dir as _get_refs_dir

RUNNING_DATA_DIR = get_running_data_dir()


def _find_references_file(activity_id: str) -> str:
    """查找references目录下的final_res_{activity_id}.json文件"""
    # 优先使用 mcp_adapter 获取的 references 目录（自动适配 skills/ 和 skills-local/）
    refs_dir = _get_refs_dir()
    primary_path = os.path.join(refs_dir, f"final_res_{activity_id}.json")
    if os.path.exists(primary_path):
        return primary_path

    # 回退：从脚本位置推导
    script_dir = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(script_dir)
    candidates = [
        os.path.join(skill_dir, "references", f"final_res_{activity_id}.json"),
    ]

    # 沙箱环境路径（同时检查 skills/ 和 skills-local/）
    if os.path.exists("/home/admin/.openclaw/workspace"):
        skill_name = os.path.basename(skill_dir)
        for skills_dir_name in ['skills-local', 'skills']:
            candidates.append(os.path.join(
                "/home/admin/.openclaw/workspace", skills_dir_name,
                skill_name, "references", f"final_res_{activity_id}.json"
            ))

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def _load_references_data(activity_id: str) -> dict:
    """
    加载references文件作为权威数据源

    Args:
        activity_id: 活动ID

    Returns:
        dict: references数据，找不到返回空dict
    """
    ref_file = _find_references_file(activity_id)
    if not ref_file:
        return {}

    try:
        with open(ref_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def parse_filename(filename: str) -> dict:
    """解析文件名，提取活动ID"""
    # 匹配 final_res_{id}.json（pipeline输出格式）
    final_res_pattern = r'final_res_(\d+)\.json$'
    match = re.search(final_res_pattern, filename)
    if match:
        return {
            "activity_id": match.group(1),
            "data_type_cn": "final_res",
            "valid": True
        }
    # 匹配 营销活动_1400064_分析结果.txt 或 已完成_营销活动_1400064_分析结果.txt
    pattern = r'营销活动_(\d+)_(.+)\.txt$'
    match = re.search(pattern, filename)

    if match:
        return {
            "activity_id": match.group(1),
            "data_type_cn": match.group(2),
            "valid": True
        }
    return {"activity_id": None, "data_type_cn": None, "valid": False}


def find_result_file(activity_id: str) -> str:
    """查找分析结果文件（优先pipeline格式，兼容旧格式）"""
    # 优先查找 references/final_res_{id}.json（pipeline输出）
    refs_file = _find_references_file(activity_id)
    if refs_file:
        return refs_file
    # 回退：旧格式 RUNNING_DATA 文件
    candidates = [
        os.path.join(RUNNING_DATA_DIR, f"营销活动_{activity_id}_分析结果.txt"),
        os.path.join(RUNNING_DATA_DIR, f"已完成_营销活动_{activity_id}_分析结果.txt"),
    ]

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def is_test_activity(data: dict) -> bool:
    """判断是否为测试活动"""
    activity_name = data.get('活动名称', '')
    plan_name = data.get('方案名称', '')
    background = data.get('方案背景', '')

    test_keywords = ['测试', 'test', 'TEST', 'demo', 'Demo', 'DEMO']
    combined_text = f"{activity_name} {plan_name} {background}"

    return any(kw in combined_text for kw in test_keywords)


def _has_valid_value(data: dict, field: str) -> bool:
    """
    检查数据中某个字段是否有有效值（非空、非空白、非空列表）

    Args:
        data: 数据字典
        field: 字段名

    Returns:
        bool: 是否有有效值
    """
    value = data.get(field)
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == '':
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    return True


def smart_repair(data: dict, activity_id: str = None) -> dict:
    """
    智能修复缺失字段

    修复优先级：
    1. references文件（8步评审流程的权威数据源）中的值
    2. 根据已有信息推断的值
    3. 默认值

    Args:
        data: 原始评审数据（RUNNING_DATA文件）
        activity_id: 活动ID（用于查找references文件）

    Returns:
        dict: {
            "repaired_data": 修复后的数据,
            "repaired_fields": 修复的字段列表,
            "repair_details": 修复详情
        }
    """
    repaired_fields = []
    repair_details = {}
    is_test = is_test_activity(data)

    # 加载references文件作为权威数据源
    ref_data = _load_references_data(activity_id)
    if ref_data:
        print(f"[INFO] 已加载references权威数据源，共 {len(ref_data)} 个字段", file=sys.stderr)

    # ====== 活动类型 ======
    if not _has_valid_value(data, '活动类型'):
        if _has_valid_value(ref_data, '活动类型'):
            data['活动类型'] = ref_data['活动类型']
            repair_details['活动类型'] = f"从references恢复: {data['活动类型']}"
        else:
            data['活动类型'] = '日常活动'
            repair_details['活动类型'] = '默认值: 日常活动'
        repaired_fields.append('活动类型')

    # ====== 获取限制 ======
    if not _has_valid_value(data, '获取限制'):
        if _has_valid_value(ref_data, '获取限制'):
            data['获取限制'] = ref_data['获取限制']
        else:
            data['获取限制'] = '未配置'
            repair_details['获取限制'] = '默认值: 未配置'
        repaired_fields.append('获取限制')

    # ====== 是否限制实名 ======
    if not _has_valid_value(data, '是否限制实名'):
        if _has_valid_value(ref_data, '是否限制实名'):
            data['是否限制实名'] = ref_data['是否限制实名']
        else:
            data['是否限制实名'] = '否'
            repair_details['是否限制实名'] = '默认值: 否'
        repaired_fields.append('是否限制实名')

    # ====== 是否限制四同（字符串格式 "通过/reason" 或 "不通过/reason"）======
    four_tong_val = data.get('是否限制四同')
    # 兼容旧格式：如果仍是dict格式（旧数据），转为字符串格式
    if isinstance(four_tong_val, dict):
        ft_pass = four_tong_val.get('pass', True)
        ft_reason = four_tong_val.get('reason', '未检查')
        data['是否限制四同'] = ('通过' if ft_pass else '不通过') + ('。' + ft_reason if ft_reason else '')
        repair_details['是否限制四同'] = f"格式转换: dict→string"
        repaired_fields.append('是否限制四同')
    elif not _has_valid_value(data, '是否限制四同'):
        if _has_valid_value(ref_data, '是否限制四同'):
            data['是否限制四同'] = ref_data['是否限制四同']
            repair_details['是否限制四同'] = '从references恢复'
        else:
            data['是否限制四同'] = '通过。未检查'
            repair_details['是否限制四同'] = '默认值: 通过'
        repaired_fields.append('是否限制四同')

    # ====== 活动配置是否合理 ======
    if not _has_valid_value(data, '活动配置是否合理'):
        if _has_valid_value(ref_data, '活动配置是否合理'):
            data['活动配置是否合理'] = ref_data['活动配置是否合理']
            repair_details['活动配置是否合理'] = f"从references恢复: {data['活动配置是否合理']}"
        else:
            data['活动配置是否合理'] = '无配置风险'
            repair_details['活动配置是否合理'] = '默认值: 无配置风险'
        repaired_fields.append('活动配置是否合理')

    # ====== 是否有风险 ======
    if not _has_valid_value(data, '是否有风险'):
        if _has_valid_value(ref_data, '是否有风险'):
            data['是否有风险'] = ref_data['是否有风险']
            repair_details['是否有风险'] = f"从references恢复: {data['是否有风险']}"
        elif data.get('活动配置是否合理') == '有配置风险':
            data['是否有风险'] = '是'
            repair_details['是否有风险'] = '根据配置风险推断: 是'
        else:
            data['是否有风险'] = '否'
            repair_details['是否有风险'] = f"{'测试活动快速判定' if is_test else '默认值'}: {data['是否有风险']}"
        repaired_fields.append('是否有风险')

    if not _has_valid_value(data, '风险判断原因'):
        if _has_valid_value(ref_data, '风险判断原因'):
            data['风险判断原因'] = ref_data['风险判断原因']
            repair_details['风险判断原因'] = '从references恢复'
        elif is_test:
            data['风险判断原因'] = '快速判定通过：测试活动，无需深度风险评审'
            repair_details['风险判断原因'] = '测试活动推断'
        elif data.get('活动配置是否合理') == '有配置风险':
            data['风险判断原因'] = '活动配置不合理，存在潜在风险'
            repair_details['风险判断原因'] = '根据配置推断'
        else:
            data['风险判断原因'] = '根据现有信息判定为低风险'
            repair_details['风险判断原因'] = '根据配置推断'
        repaired_fields.append('风险判断原因')

    # ====== 防控模块（保持为空，由Rone人工填写）======
    if not _has_valid_value(data, '防控模块推荐'):
        data['防控模块推荐'] = []
        repair_details['防控模块推荐'] = '默认值: 暂不配置'
        repaired_fields.append('防控模块推荐')

    if not _has_valid_value(data, '感知模块推荐'):
        data['感知模块推荐'] = []
        repair_details['感知模块推荐'] = '默认值: 暂不配置'
        repaired_fields.append('感知模块推荐')

    return {
        "repaired_data": data,
        "repaired_fields": list(set(repaired_fields)),  # 去重
        "repair_details": repair_details,
        "is_test_activity": is_test
    }


def validate_result(data: dict) -> dict:
    """验证修复后的完整性"""

    # 必需字段列表（与 check_and_repair.py REQUIRED_FIELDS 对齐，基于「数金一页纸内容」文档）
    required_fields = [
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

    missing = []
    for field in required_fields:
        if not _has_valid_value(data, field):
            missing.append(field)

    return {
        "is_complete": len(missing) == 0,
        "missing_fields": missing,
        "total_fields": len(required_fields),
        "present_fields": len(required_fields) - len(missing)
    }


def repair_and_save(activity_id: str, dry_run: bool = False) -> dict:
    """
    修复活动结果并保存

    Args:
        activity_id: 活动ID
        dry_run: 是否仅预览，不实际写入

    Returns:
        dict: 修复结果
    """
    # 查找结果文件
    result_file = find_result_file(activity_id)
    if not result_file:
        return {
            "success": False,
            "error": f"找不到活动 {activity_id} 的分析结果文件",
            "activity_id": activity_id
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
            "file": result_file
        }

    # 验证修复前
    before_validation = validate_result(data)

    # 执行修复
    repair_result = smart_repair(data, activity_id)
    repaired_data = repair_result["repaired_data"]

    # 验证修复后
    after_validation = validate_result(repaired_data)

    # 保存（如果不是 dry_run）
    if not dry_run:
        try:
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(repaired_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {
                "success": False,
                "error": f"写入文件失败: {e}",
                "activity_id": activity_id,
                "file": result_file
            }

    return {
        "success": True,
        "activity_id": activity_id,
        "file": result_file,
        "dry_run": dry_run,
        "is_test_activity": repair_result["is_test_activity"],
        "before": {
            "is_complete": before_validation["is_complete"],
            "missing_fields": before_validation["missing_fields"]
        },
        "after": {
            "is_complete": after_validation["is_complete"],
            "missing_fields": after_validation["missing_fields"]
        },
        "repaired_fields": repair_result["repaired_fields"],
        "repair_details": repair_result["repair_details"],
        "message": f"修复完成，共修复 {len(repair_result['repaired_fields'])} 个字段" if repair_result['repaired_fields'] else "无需修复，所有字段已完整"
    }


def main():
    parser = argparse.ArgumentParser(
        description='评审结果自动修复脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python3 repair_result.py --activity-id 1400064
    python3 repair_result.py --activity-id 1400064 --dry-run
    python3 repair_result.py --file "营销活动_1400064_分析结果.txt"
        '''
    )
    parser.add_argument('--activity-id', '-a', type=str, help='活动ID')
    parser.add_argument('--file', '-f', type=str, help='直接指定结果文件路径')
    parser.add_argument('--dry-run', '-n', action='store_true', help='仅预览修复结果，不实际写入')
    parser.add_argument('--json', '-j', action='store_true', help='仅输出JSON格式')

    args = parser.parse_args()

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

    # 执行修复
    result = repair_and_save(activity_id, args.dry_run)

    # 输出结果
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 打印详细报告
        if result.get('success'):
            print("\n" + "=" * 60, file=sys.stderr)
            print("📋 修复报告", file=sys.stderr)
            print("=" * 60, file=sys.stderr)

            if result.get('is_test_activity'):
                print("🔹 检测到测试活动，使用测试活动默认值", file=sys.stderr)

            if result.get('repaired_fields'):
                print(f"\n✅ 已修复字段 ({len(result['repaired_fields'])} 个):", file=sys.stderr)
                for field in result['repaired_fields']:
                    detail = result['repair_details'].get(field, '')
                    print(f"   • {field}: {detail}", file=sys.stderr)
            else:
                print("\n✅ 所有字段已完整，无需修复", file=sys.stderr)

            print("\n" + "=" * 60, file=sys.stderr)

    sys.exit(0 if result.get('success') else 1)


if __name__ == '__main__':
    main()