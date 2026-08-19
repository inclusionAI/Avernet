#!/usr/bin/env python3
"""
Pipeline 入口脚本 - 读取 event_property，运行规则引擎，输出 final_res

用法:
    python3 run_pipeline.py --activity-id 5100225
    python3 run_pipeline.py --event-property /path/to/event_property_5100225.json
    python3 run_pipeline.py --event-property /path/to/ep.json --output /path/to/output.json

输出:
    {"success": true, "activity_id": "5100225", "fields_count": 39, "output_path": "..."}
"""

import json
import os
import sys
import argparse

# 确保 skills 同级目录在 sys.path 中（.claude/skills/）
# skill 目录名使用 kebab-case（如 biz-risk-check），但 Python 模块名需用下划线，
# 因此通过 importlib.util 显式加载并注册下划线别名使 import 兼容
import importlib
import importlib.util

_script_dir = os.path.dirname(os.path.abspath(__file__))
_pipeline_dir = os.path.dirname(_script_dir)
_skills_root = os.path.dirname(_pipeline_dir)

# 沙箱环境可能部署在 skills-local/ 或 skills/ 下，兄弟 skill 可能在另一个目录
# 因此同时搜索两个目录
_SANDBOX_WORKSPACE = "/home/admin/.openclaw/workspace"
_skills_dirs = [_skills_root]
if os.path.isdir(_skills_root):
    _parent = os.path.dirname(_skills_root)
    # 如果当前在 skills-local/，补上 skills/（反之亦然）
    _basename = os.path.basename(_skills_root.rstrip('/'))
    if _basename == 'skills-local':
        _sibling = os.path.join(_parent, 'skills')
    elif _basename == 'skills':
        _sibling = os.path.join(_parent, 'skills-local')
    else:
        _sibling = None
    if _sibling and os.path.isdir(_sibling) and _sibling not in _skills_dirs:
        _skills_dirs.append(_sibling)
# 也检查沙箱标准路径（兼容从其他位置调用的情况）
for _std in [os.path.join(_SANDBOX_WORKSPACE, 'skills-local'),
             os.path.join(_SANDBOX_WORKSPACE, 'skills')]:
    if os.path.isdir(_std) and _std not in _skills_dirs:
        _skills_dirs.append(_std)

for _sd in _skills_dirs:
    if _sd not in sys.path:
        sys.path.insert(0, _sd)

_KEBAB_TO_UNDERSCORE = {
    'biz-risk-check': 'biz_risk_check',
    'biz-scenario-recognition': 'biz_scenario_recognition',
    'config-risk-check': 'config_risk_check',
    'data-preprocessing': 'data_preprocessing',
    'gameplay-recognition': 'gameplay_recognition',
    'overall-risk-check': 'overall_risk_check',
    'prize-value-recognition': 'prize_value_recognition',
}
for _kdir, _umod in _KEBAB_TO_UNDERSCORE.items():
    if _umod in sys.modules:
        continue
    # 在所有 skills 目录中查找
    _kpath = None
    for _sd in _skills_dirs:
        _candidate = os.path.join(_sd, _kdir)
        if os.path.isdir(_candidate):
            _kpath = _candidate
            break
    if not _kpath:
        continue
    # 创建一个 namespace package 并注册为下划线模块名
    _mod = importlib.import_module(_kdir)
    sys.modules[_umod] = _mod

from overall_risk_check.processor import run_pipeline, to_final_res
from mcp_adapter import get_references_dir, build_filename, parse_filename


def find_event_property(activity_id: str) -> str:
    """根据 activity_id 查找 event_property 文件"""
    refs_dir = get_references_dir()

    # 优先查找带 activity_id 的文件
    candidates = [
        os.path.join(refs_dir, f"event_property_{activity_id}.json"),
        os.path.join(refs_dir, "event_property.json"),
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        f"找不到 event_property 文件 (activity_id={activity_id})\n"
        f"尝试过的路径: {candidates}"
    )


def run(event_property_path: str, output_path: str = None, activity_id: str = None) -> dict:
    """
    运行完整 pipeline 并输出 final_res

    Args:
        event_property_path: event_property JSON 文件路径
        output_path: 输出路径（可选）
        activity_id: 活动ID（可选，用于命名输出文件）

    Returns:
        dict: 运行结果
    """
    # 读取 event_property
    with open(event_property_path, 'r', encoding='utf-8') as f:
        ep_data = json.load(f)

    # 兼容 MCP 格式
    if 'examinationBasicInfo' not in ep_data and 'campBasicInfo_new' in ep_data:
        # MCP 格式：需要包装并重命名
        if 'campBasicInfoNew' not in ep_data:
            ep_data['campBasicInfoNew'] = ep_data.pop('campBasicInfo_new', {})
            # 也在顶层设置
            if 'campBasicInfoNew' not in ep_data.get('examinationBasicInfo', {}):
                pass  # preprocess_row 内部会处理
        ep_wrapped = {'examinationBasicInfo': ep_data}
        ep_str = json.dumps(ep_wrapped, ensure_ascii=False)
    elif 'examinationBasicInfo' in ep_data:
        ep_str = json.dumps(ep_data, ensure_ascii=False)
    else:
        ep_str = json.dumps(ep_data, ensure_ascii=False)

    # 运行 pipeline
    row = {'event_property': ep_str}
    pipeline_result = run_pipeline(row)

    # 提取 activity_id
    if not activity_id:
        activity_id = pipeline_result.get('activity_id', '')
        if not activity_id:
            # 从文件名提取
            import re
            match = re.search(r'(\d+)', event_property_path)
            if match:
                activity_id = match.group(1)

    # 转换为 final_res
    final_res = to_final_res(pipeline_result)

    # 确定输出路径
    refs_dir = get_references_dir()
    os.makedirs(refs_dir, exist_ok=True)
    if not output_path:
        if activity_id:
            output_path = os.path.join(refs_dir, f"final_res_{activity_id}.json")
        else:
            output_path = os.path.join(refs_dir, "final_res.json")

    # 保存
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(final_res, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "activity_id": activity_id,
        "fields_count": len(final_res),
        "has_risk": pipeline_result.get('has_risk', False),
        "risk_summary": pipeline_result.get('risk_summary', ''),
        "gameplay_names": pipeline_result.get('gameplay_names', []),
        "output_path": output_path,
    }


def main():
    parser = argparse.ArgumentParser(description='运行营销活动评审 Pipeline')
    parser.add_argument('--activity-id', '-a', type=str,
                        help='活动ID，自动查找 event_property_{id}.json')
    parser.add_argument('--event-property', '-e', type=str,
                        help='event_property JSON 文件路径')
    parser.add_argument('--output', '-o', type=str,
                        help='输出文件路径（默认: references/final_res_{id}.json）')

    args = parser.parse_args()

    # 确定输入文件
    if args.event_property:
        ep_path = args.event_property
        activity_id = args.activity_id or ''
    elif args.activity_id:
        activity_id = args.activity_id
        try:
            ep_path = find_event_property(activity_id)
        except FileNotFoundError as e:
            print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
            sys.exit(1)
    else:
        print(json.dumps({
            "success": False,
            "error": "需要提供 --activity-id 或 --event-property 参数"
        }, ensure_ascii=False))
        sys.exit(1)

    # 运行
    try:
        result = run(ep_path, output_path=args.output, activity_id=activity_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 同时写入标准 RUNNING_DATA 位置的分析结果文件（兼容后续步骤）
        from mcp_adapter import get_running_data_dir
        running_data = get_running_data_dir()
        if activity_id:
            result_file = os.path.join(running_data, f"营销活动_{activity_id}_分析结果.txt")
            with open(result_file, 'w', encoding='utf-8') as f:
                # 读取刚才写入的 final_res
                with open(result['output_path'], 'r', encoding='utf-8') as rf:
                    final_res_data = json.load(rf)
                json.dump(final_res_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()