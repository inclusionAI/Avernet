#!/usr/bin/env python3
"""
评审结果完整性校验脚本

在所有评审步骤完成后执行，检查必需字段是否都存在。
如果缺少字段，输出缺失字段及其来源步骤，方便追溯和补救。

用法:
    python3 scripts/validate_result.py --activity-id 300005
    python3 scripts/validate_result.py --file references/final_res_300005.json

输出格式:
    {
        "is_complete": true/false,
        "missing_fields": [...],
        "completed_steps": [...],
        "missing_steps": [...],
        "message": "..."
    }

退出码:
    0 - 完整性校验通过
    1 - 缺少必需字段
    2 - 文件不存在或参数错误
"""

import json
import os
import sys
import argparse
import glob

# 统一使用 mcp_adapter 获取 references 目录
from mcp_adapter import get_references_dir as _get_refs_dir

# 必需字段定义：字段名 -> (来源, 步骤编号, 必需程度)
# 必需程度: REQUIRED(必须) | CONDITIONAL(条件必需) | OPTIONAL(可选)
# 与 to_final_res() 输出字段对齐（基于「数金一页纸内容」文档）
REQUIRED_FIELDS = {
    # 基础要素
    '活动名称': ('pipeline', '步骤1-5', 'REQUIRED'),
    '活动CP号': ('pipeline', '步骤1-5', 'REQUIRED'),

    # 活动限制
    '活动类型': ('pipeline', '步骤6', 'REQUIRED'),
    '获取限制': ('pipeline', '步骤6', 'REQUIRED'),
    '是否限制实名': ('pipeline', '步骤6', 'REQUIRED'),
    '是否限制四同': ('pipeline', '步骤6', 'REQUIRED'),
    '是否地域限制': ('pipeline', '步骤6', 'OPTIONAL'),

    # 评审结论
    '活动配置是否合理': ('pipeline', '步骤6', 'REQUIRED'),
    '活动配置是否合理原因': ('pipeline', '步骤6', 'CONDITIONAL'),
    '详细校验结果': ('pipeline', '步骤6', 'OPTIONAL'),
    '是否有风险': ('pipeline', '步骤6', 'REQUIRED'),
    '风险判断原因': ('pipeline', '步骤6', 'REQUIRED'),
}

# 步骤完成标记字段
STEP_MARKERS = {
    'data-fetcher': '活动名称',
    'pipeline': '是否有风险',
}


def find_refs_file(activity_id: str) -> str:
    """查找 references 目录下的 final_res 文件"""
    # 优先使用 mcp_adapter 获取的 references 目录（自动适配 skills/ 和 skills-local/）
    refs_dir = _get_refs_dir()
    primary_path = os.path.join(refs_dir, f"final_res_{activity_id}.json")
    if os.path.exists(primary_path):
        return primary_path

    # 回退：从脚本位置推导
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "..", "references", f"final_res_{activity_id}.json"),
        f"references/final_res_{activity_id}.json",
    ]

    # 沙箱环境路径（同时检查 skills/ 和 skills-local/）
    if os.path.exists("/home/admin/.openclaw/workspace"):
        skill_name = os.path.basename(os.path.dirname(script_dir))
        for skills_dir_name in ['skills-local', 'skills']:
            sandbox_path = os.path.join(
                "/home/admin/.openclaw/workspace", skills_dir_name,
                skill_name, "references", f"final_res_{activity_id}.json"
            )
            candidates.append(sandbox_path)

    for path in candidates:
        if os.path.exists(path):
            return path

    # 尝试 glob 匹配
    for pattern in [f"**/final_res_{activity_id}.json", f"**/references/final_res_{activity_id}.json"]:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    return None


def validate_result(data: dict, activity_id: str = None) -> dict:
    """
    验证评审结果完整性

    Args:
        data: 评审结果数据
        activity_id: 活动ID（用于错误消息）

    Returns:
        dict: 校验结果
    """
    missing_required = []  # 缺失的必需字段
    missing_conditional = []  # 缺失的条件必需字段
    present_fields = []  # 已存在的字段
    completed_steps = []  # 已完成的步骤
    missing_steps = []  # 未完成的步骤

    # 检查每个步骤是否完成
    for step_name, marker_field in STEP_MARKERS.items():
        if marker_field in data and data[marker_field]:
            completed_steps.append(step_name)
        else:
            missing_steps.append(step_name)

    # 检查每个必需字段
    for field, (step, step_num, required_level) in REQUIRED_FIELDS.items():
        field_value = data.get(field)

        # 判断字段是否存在且非空
        field_exists = field in data and field_value is not None and field_value != ''

        if field_exists:
            present_fields.append(field)
        else:
            if required_level == 'REQUIRED':
                missing_required.append({
                    'field': field,
                    'step': step,
                    'step_num': step_num,
                    'reason': '字段缺失或为空'
                })
            elif required_level == 'CONDITIONAL':
                # 条件必需字段，根据上下文判断是否真的需要
                missing_conditional.append({
                    'field': field,
                    'step': step,
                    'step_num': step_num,
                    'reason': '条件必需字段缺失'
                })

    # 根据风险判定结果调整条件必需字段
    # 如果配置不合理，则"活动配置是否合理原因"是必需的
    if data.get('活动配置是否合理') == '有配置风险':
        for item in missing_conditional:
            if item['field'] == '活动配置是否合理原因':
                missing_required.append(item)
                break

    # 生成消息
    if not missing_required:
        message = f"完整性校验通过，共 {len(present_fields)} 个字段，{len(completed_steps)} 个步骤已完成"
    else:
        message = f"缺少 {len(missing_required)} 个必需字段，涉及 {len(set(m['step'] for m in missing_required))} 个步骤"

    return {
        'activity_id': activity_id,
        'is_complete': len(missing_required) == 0,
        'present_fields_count': len(present_fields),
        'missing_required': missing_required,
        'missing_conditional': missing_conditional,
        'completed_steps': completed_steps,
        'missing_steps': missing_steps,
        'message': message,
        'required_fields_total': len([f for f, (_, _, r) in REQUIRED_FIELDS.items() if r == 'REQUIRED']),
        'suggested_actions': generate_suggested_actions(missing_required, missing_steps)
    }


def generate_suggested_actions(missing_required: list, missing_steps: list) -> list:
    """生成补救建议"""
    actions = []

    # 按步骤分组
    step_fields = {}
    for item in missing_required:
        step = item['step']
        if step not in step_fields:
            step_fields[step] = []
        step_fields[step].append(item['field'])

    # 生成建议
    step_skill_map = {
        'pipeline': 'python3 scripts/run_pipeline.py',
        'data-fetcher': 'python3 scripts/fetch_data.py',
    }

    for step, fields in step_fields.items():
        skill = step_skill_map.get(step, step)
        actions.append({
            'step': step,
            'skill': skill,
            'missing_fields': fields,
            'action': f"执行 {skill} 补充缺失字段: {', '.join(fields)}"
        })

    return actions


def print_report(result: dict):
    """打印可读性报告"""
    print("\n" + "=" * 60)
    print("📊 评审结果完整性校验报告")
    print("=" * 60)

    if result.get('activity_id'):
        print(f"活动ID: {result['activity_id']}")

    # 整体状态
    if result['is_complete']:
        print(f"\n✅ 校验通过: {result['message']}")
    else:
        print(f"\n❌ 校验失败: {result['message']}")

    # 步骤完成情况
    print(f"\n📋 步骤完成情况:")
    print(f"   已完成: {len(result['completed_steps'])} 个 - {', '.join(result['completed_steps']) or '无'}")
    print(f"   未完成: {len(result['missing_steps'])} 个 - {', '.join(result['missing_steps']) or '无'}")

    # 缺失字段详情
    if result['missing_required']:
        print(f"\n⚠️ 缺失的必需字段 ({len(result['missing_required'])} 个):")
        for item in result['missing_required']:
            print(f"   • {item['field']} (来源: {item['step_num']} - {item['step']})")

    if result['missing_conditional']:
        print(f"\n⚠️ 缺失的条件必需字段 ({len(result['missing_conditional'])} 个):")
        for item in result['missing_conditional']:
            print(f"   • {item['field']} (来源: {item['step_num']} - {item['step']})")

    # 补救建议
    if result.get('suggested_actions'):
        print(f"\n💡 补救建议:")
        for action in result['suggested_actions']:
            print(f"   {action['step']}: 执行 {action['skill']}")
            print(f"      缺失字段: {', '.join(action['missing_fields'])}")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='评审结果完整性校验',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python3 validate_result.py --activity-id 300005
    python3 validate_result.py --file references/final_res_300005.json
    python3 validate_result.py --activity-id 300005 --json  # 仅输出JSON
        '''
    )
    parser.add_argument('--activity-id', '-a', type=str, help='活动ID')
    parser.add_argument('--file', '-f', type=str, help='直接指定结果文件路径')
    parser.add_argument('--json', '-j', action='store_true', help='仅输出JSON格式，不打印报告')

    args = parser.parse_args()

    # 确定文件路径
    file_path = None
    activity_id = None

    if args.file:
        file_path = args.file
        # 从文件名提取活动ID
        import re
        match = re.search(r'final_res_(\d+)', args.file)
        if match:
            activity_id = match.group(1)
    elif args.activity_id:
        activity_id = args.activity_id
        file_path = find_refs_file(activity_id)
    else:
        # 尝试自动查找最新的 final_res 文件
        refs_dir = _get_refs_dir()
        patterns = [
            os.path.join(refs_dir, "final_res_*.json"),
            "references/final_res_*.json",
        ]
        # 沙箱环境路径（同时检查 skills/ 和 skills-local/）
        if os.path.exists("/home/admin/.openclaw/workspace"):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            skill_name = os.path.basename(os.path.dirname(script_dir))
            for skills_dir_name in ['skills-local', 'skills']:
                patterns.append(os.path.join(
                    "/home/admin/.openclaw/workspace", skills_dir_name,
                    skill_name, "references", "final_res_*.json"
                ))
        for pattern in patterns:
            matches = glob.glob(pattern)
            if matches:
                file_path = max(matches, key=os.path.getmtime)
                match = re.search(r'final_res_(\d+)', file_path)
                if match:
                    activity_id = match.group(1)
                break

    if not file_path or not os.path.exists(file_path):
        result = {
            'is_complete': False,
            'error': '文件不存在',
            'message': f'找不到评审结果文件 (activity_id={activity_id})',
            'file_path': file_path
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    # 读取数据
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        result = {
            'is_complete': False,
            'error': 'JSON解析失败',
            'message': str(e),
            'file_path': file_path
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    # 执行校验
    result = validate_result(data, activity_id)
    result['file_path'] = file_path

    # 输出结果
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print_report(result)

    # 设置退出码
    sys.exit(0 if result['is_complete'] else 1)


if __name__ == '__main__':
    main()