#!/usr/bin/env python3
"""
ODPS 审批 - 自动通过检查（workflow action 版本）
基于 auto_check.py，集成 validate_auto.py 校验逻辑。

用法：
    python3 auto_check.py --input <context_json_path_or_string> [--config <config_path>]
"""

import argparse
import json
import os
import sys


# ── 校验逻辑（集成自 validate_auto.py）──────────────────────────

VALID_AUTO_IDS = {f"AUTO-00{i}" for i in range(1, 9)}
VALID_NEXT_ACTIONS = {"skip_to_decision", "continue_step2"}


def validate_auto_check(data: dict) -> list:
    """校验 auto_check 输出。"""
    errors = []

    auto_pass = data.get("auto_pass")
    next_action = data.get("next_action", "")
    matched_rule = data.get("matched_rule", "")
    reason = data.get("reason", "")

    if not isinstance(auto_pass, bool):
        errors.append({
            "level": "BLOCK", "code": "INVALID_AUTO_PASS",
            "msg": f"auto_pass 必须为布尔值，实际为 {type(auto_pass).__name__}"
        })

    if next_action not in VALID_NEXT_ACTIONS:
        errors.append({
            "level": "BLOCK", "code": "INVALID_NEXT_ACTION",
            "msg": f"next_action={next_action} 不合法（应为 {sorted(VALID_NEXT_ACTIONS)}）"
        })

    if auto_pass is True:
        if not matched_rule:
            errors.append({
                "level": "BLOCK", "code": "AUTO_PASS_NO_MATCH",
                "msg": "auto_pass=true 但 matched_rule 为空"
            })
        elif matched_rule not in VALID_AUTO_IDS:
            errors.append({
                "level": "BLOCK", "code": "INVALID_AUTO_ID",
                "msg": f"matched_rule={matched_rule} 不在白名单 AUTO-001~008"
            })
        if not reason:
            errors.append({
                "level": "WARN", "code": "NO_REASON",
                "msg": "auto_pass=true 但缺少 reason 说明"
            })
        if next_action != "skip_to_decision":
            errors.append({
                "level": "BLOCK", "code": "PASS_BUT_NOT_SKIP",
                "msg": f"auto_pass=true 但 next_action={next_action}，应为 skip_to_decision"
            })
    else:
        if next_action != "continue_step2":
            errors.append({
                "level": "BLOCK", "code": "NO_PASS_BUT_SKIP",
                "msg": f"auto_pass=false 但 next_action={next_action}，应为 continue_step2"
            })

    return errors


# ── 核心逻辑 ────────────────────────────────────────────────────

SPECIAL_ASSETS = {"LBS"}
LOW_SENSITIVITY_LEVELS = {"L1", "L2", "B2", "C1", "00", ""}
FUNCTIONAL_DEPARTMENTS = {"大安全", "客权", "反洗钱"}
ZHIMA_SUBJECTS = set()


def extract_department(owner_info_str):
    """从 executeProjectOwnerInfo / projectOwnerInfo 字符串中提取一级部门。"""
    if not owner_info_str or not isinstance(owner_info_str, str):
        return ""
    parts = owner_info_str.split("-")
    if len(parts) < 4:
        return ""
    return parts[3].strip()


def evaluate_auto_rules(context):
    """跑 AUTO-001~008，命中第一条即返回。"""
    apply_subject = context.get("applySubjectInfo", {})
    use_subject = context.get("useSubjectInfo", {})
    approve_params = context.get("approveParams", {})
    table_info = context.get("tableInfo", {})

    apply_code = apply_subject.get("subjectCode", "")
    use_code = use_subject.get("subjectCode", "")
    account_type = approve_params.get("accountType", "")
    category = approve_params.get("category", "")
    max_level = table_info.get("maxDataLevel", "")
    table_biz_desc = approve_params.get("tableBizDomainDesc", "") or ""

    apply_dept = extract_department(approve_params.get("executeProjectOwnerInfo", ""))
    data_owner_dept = extract_department(approve_params.get("projectOwnerInfo", ""))

    # AUTO-001~003: 职能部门生产账号域内互通
    if account_type == "PRODUCT_ACCOUNT":
        for dept_keyword, auto_id, auto_name in [
            ("大安全", "AUTO-001", "大安全域生产账号互通"),
            ("客权", "AUTO-002", "客权域生产账号互通"),
            ("反洗钱", "AUTO-003", "反洗钱域生产账号互通"),
        ]:
            if dept_keyword in apply_dept and dept_keyword in data_owner_dept:
                return {
                    "auto_id": auto_id,
                    "auto_name": auto_name,
                    "reason": f"账号类型=PRODUCT_ACCOUNT, 申请人部门含'{dept_keyword}', 数据owner部门含'{dept_keyword}'",
                }

    # AUTO-004: Z97 → Z50（非特殊资产）
    if apply_code == "zhifubaoZ97" and use_code == "zhifubao":
        is_special = any(s in table_biz_desc for s in SPECIAL_ASSETS)
        if not is_special:
            return {
                "auto_id": "AUTO-004",
                "auto_name": "Z97 数据流入 Z50",
                "reason": f"申请方=zhifubaoZ97, 接收方=zhifubao, tableBizDomainDesc='{table_biz_desc[:50]}' 非特殊资产",
            }

    # AUTO-005: Z50 → Z97（≤L2 低敏）
    if apply_code == "zhifubao" and use_code == "zhifubaoZ97":
        if max_level in LOW_SENSITIVITY_LEVELS:
            return {
                "auto_id": "AUTO-005",
                "auto_name": "Z50 数据流入 Z97（低敏）",
                "reason": f"申请方=zhifubao, 接收方=zhifubaoZ97, maxDataLevel={max_level or '未知(默认低敏)'}",
            }

    # AUTO-006: Z51 支付/营销 → Z50
    if apply_code == "antgroupZ51" and use_code == "zhifubao" and category in ("支付", "营销"):
        return {
            "auto_id": "AUTO-006",
            "auto_name": "Z51 支付/营销 PJ 进 Z50",
            "reason": f"申请方=antgroupZ51, 接收方=zhifubao, 类别={category}",
        }

    # AUTO-007: Z97 → 芝麻
    if apply_code == "zhifubaoZ97" and use_code in ZHIMA_SUBJECTS:
        return {
            "auto_id": "AUTO-007",
            "auto_name": "Z97 数据进芝麻",
            "reason": f"申请方=zhifubaoZ97, 接收方={use_code}（芝麻）",
        }

    # AUTO-008: Z97 → 逸康
    if apply_code == "zhifubaoZ97" and use_code == "antyikang":
        return {
            "auto_id": "AUTO-008",
            "auto_name": "Z97 数据进逸康",
            "reason": f"申请方=zhifubaoZ97, 接收方=antyikang（蚂蚁逸康）",
        }

    return None


def _default_config_path():
    """基于脚本位置计算 pack 内默认 config 路径（绝对路径）。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', '..', 'config', 'config.json')


def parse_workflow_args():
    """兼容 workflow engine --workflow-engine-args-json 和直接命令行两种调用方式。"""
    _default_cfg = _default_config_path()
    parser = argparse.ArgumentParser(description="ODPS 审批 - 自动通过检查（workflow action）")
    # workflow engine 协议参数
    parser.add_argument("--workflow-engine-action", default=None)
    parser.add_argument("--workflow-engine-args-json", default=None)
    parser.add_argument("--workflow-engine-context-json", default=None)
    # 兼容直接命令行调用
    parser.add_argument("--input", default=None, help="context JSON 文件路径或字符串")
    parser.add_argument("--config", default=None, help="配置文件路径")
    args = parser.parse_args()

    if args.workflow_engine_args_json is not None:
        # workflow engine 调用：从 JSON 中提取参数
        action_args = json.loads(args.workflow_engine_args_json)
        context = json.loads(args.workflow_engine_context_json or "{}")
        input_val = action_args.get("input", "")
        config_path = action_args.get("config") or context.get("config") or _default_cfg
        return {
            "input": input_val,
            "config": config_path,
        }
    else:
        # 直接命令行调用
        return {
            "input": args.input,
            "config": args.config or _default_cfg,
        }


def main():
    action_args = parse_workflow_args()
    input_val = action_args["input"]
    if not input_val:
        print(json.dumps({"error": "缺少 --input 参数"}, ensure_ascii=False))
        sys.exit(1)
    if not isinstance(input_val, dict):
        print(json.dumps({"error": f"input 必须为 dict，实际为 {type(input_val).__name__}"}, ensure_ascii=False))
        sys.exit(1)
    data = input_val

    context = data.get("context", data)
    matched = evaluate_auto_rules(context)

    if matched:
        output = {
            "auto_pass": True,
            "matched_rule": matched["auto_id"],
            "matched_rule_name": matched["auto_name"],
            "reason": matched["reason"],
            "next_action": "skip_to_decision",
            "engine_version": "v2.1"
        }
    else:
        output = {
            "auto_pass": False,
            "evaluated_rules": ["AUTO-001", "AUTO-002", "AUTO-003", "AUTO-004", "AUTO-005", "AUTO-006", "AUTO-007", "AUTO-008"],
            "next_action": "continue_step2",
            "engine_version": "v2.1"
        }

    # 集成校验
    errors = validate_auto_check(output)
    has_block = any(e["level"] == "BLOCK" for e in errors)
    if has_block:
        output["validation_errors"] = errors
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    if errors:
        output["validation_warnings"] = [e for e in errors if e["level"] == "WARN"]

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
