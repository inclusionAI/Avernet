#!/usr/bin/env python3
"""
ODPS 审批 - 获取申请信息（workflow action 版本）
基于 fetch_context.py，集成 validate_context.py 校验逻辑。

用法：
    python3 fetch_context.py --config <config_path> --application-id <id> [--task-content <json>]
"""

import argparse
import json
import os
import sys

# 支持 workflow action 调用和直接命令行调用
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'config'))
try:
    from mcp_utils import load_config, run_mcp_call
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config'))
    from mcp_utils import load_config, run_mcp_call


# ── 校验逻辑（集成自 validate_context.py）──────────────────────

def validate_context(data: dict) -> list:
    """校验 fetch_context 输出，BLOCK 级错误以非零退出码终止。"""
    errors = []
    context = data.get("context", data)

    if data.get("skipped") is True:
        errors.append({
            "level": "BLOCK", "code": "SKIPPED_APPLICATION",
            "msg": f"审批单已跳过: {data.get('reason', '未知原因')}，禁止进入下一阶段"
        })
        return errors

    required_fields = ["applicationId", "approveStatus", "columns", "approveParams", "tableInfo"]
    for field in required_fields:
        if field not in context or context.get(field) is None:
            errors.append({
                "level": "BLOCK", "code": "MISSING_FIELD",
                "msg": f"context_json 缺少必需字段: {field}"
            })

    status = context.get("approveStatus", "")
    if status and status != "APPROVING":
        errors.append({
            "level": "BLOCK", "code": "INVALID_STATUS",
            "msg": f"审批单状态为 {status}，非 APPROVING，禁止继续处理"
        })

    if "columns" in context and not isinstance(context["columns"], list):
        errors.append({
            "level": "BLOCK", "code": "INVALID_COLUMNS",
            "msg": "columns 字段必须是数组"
        })

    # 兼容两种 API 返回格式检查审批人
    approvers = context.get("currentApprovers", [])
    approver_staff_no_str = context.get("currentApproverStaffNo", "")
    
    # 如果数组为空且字符串也为空，才报 warning
    has_approvers = False
    if isinstance(approvers, list) and len(approvers) > 0:
        has_approvers = True
    if isinstance(approver_staff_no_str, str) and approver_staff_no_str.strip():
        has_approvers = True
        
    if not has_approvers:
        errors.append({
            "level": "WARN", "code": "NO_APPROVERS",
            "msg": "当前审批人列表为空，请确认审批单是否已流转"
        })

    return errors


# ── 核心逻辑 ────────────────────────────────────────────────────

def extract_department(owner_info_str):
    """从 executeProjectOwnerInfo / projectOwnerInfo 字符串中提取一级部门。"""
    if not owner_info_str or not isinstance(owner_info_str, str):
        return ""
    parts = owner_info_str.split("-")
    if len(parts) < 4:
        return ""
    return parts[3].strip()


def verify_assignee(task_content, config):
    """Step 0: 任务归属校验。"""
    if not task_content:
        return True, None, None

    try:
        content = json.loads(task_content) if isinstance(task_content, str) else task_content
    except json.JSONDecodeError as e:
        return False, f"task_content JSON 解析失败: {e}", None

    dgd_staff_no = config.get("dgd_staff_no", "")
    if not dgd_staff_no:
        return False, "config.dgd_staff_no 未配置，无法校验任务归属", None

    assignee_id = content.get("assigneeId", "")
    if assignee_id and str(assignee_id) != str(dgd_staff_no):
        return False, f"任务 assigneeId={assignee_id} 与本地 dgd_staff_no={dgd_staff_no} 不一致", None

    approve_staff_nos = content.get("approveStaffNos") or []
    if isinstance(approve_staff_nos, str):
        approve_staff_nos = [s.strip() for s in approve_staff_nos.split(",") if s.strip()]
    if approve_staff_nos and str(dgd_staff_no) not in [str(s) for s in approve_staff_nos]:
        return False, f"任务 approveStaffNos={approve_staff_nos} 不包含本地 dgd_staff_no={dgd_staff_no}", None

    extra_data = content.get("extraData") or content.get("extra_data") or {}
    return True, None, extra_data


def verify_approver(application_id, config):
    """前置校验：确认审批单状态和审批人。"""
    ds_server = config["mcp_server_ds"]
    staff_no = config["dgd_staff_no"]

    result = run_mcp_call(ds_server, "get_application_detail", {
        "applicationId": application_id,
        "submitter": staff_no
    }, config)

    if result is None:
        return False, "get_application_detail 调用失败", None

    data = result.get("data", {})
    if not data:
        return False, "get_application_detail 返回空数据", None

    approve_status = data.get("approveStatus", "")
    if approve_status != "APPROVING":
        return False, f"审批单状态为 {approve_status}，非 APPROVING", data

    # 兼容两种 API 返回格式：
    # 1. getApplicationFullDetail 返回: currentApprovers = [{"staffNo": "xxx"}, ...]
    # 2. get_application_detail 返回: currentApproverStaffNo = "xxx,yyy" (逗号分隔字符串)
    approver_ids = []
    
    # 尝试格式1: currentApprovers 数组
    current_approvers = data.get("currentApprovers", [])
    if isinstance(current_approvers, list) and len(current_approvers) > 0:
        approver_ids = [str(a.get("staffNo", a.get("staff_no", ""))) for a in current_approvers if isinstance(a, dict)]
    
    # 尝试格式2: currentApproverStaffNo 字符串 (如 "422737,WB823585")
    if not approver_ids:
        staff_no_str = data.get("currentApproverStaffNo", "")
        if isinstance(staff_no_str, str) and staff_no_str:
            approver_ids = [s.strip() for s in staff_no_str.split(",") if s.strip()]
    
    # 校验是否在审批人列表中
    if approver_ids and str(staff_no) not in approver_ids:
        return False, f"当前审批人不包含 {staff_no}，审批可能已流转", data

    return True, None, data


def fetch_full_detail(application_id, config):
    """获取申请单完整详情。"""
    ds_server = config["mcp_server_ds"]

    result = run_mcp_call(ds_server, "getApplicationFullDetail", {
        "applicationId": application_id,
        "includeApproverInfo": True,
        "includeColumns": True,
        "includeApproveParams": True,
        "includeSubjectInfo": True,
        "includeTablePIIInfo": True
    }, config)

    if result is None:
        return None

    return result.get("data", {})


def build_context(detail, status_detail, extra_data=None, config=None):
    """构建上下文 JSON。"""
    context = {
        "applicationId": detail.get("applicationId", ""),
        "approveStatus": status_detail.get("approveStatus", "APPROVING"),
        "approveUrl": detail.get("approveUrl", status_detail.get("approveUrl", "")),
        "currentNodeName": detail.get("currentNodeName", status_detail.get("currentNodeName", "")),
    }

    # 添加申请人信息（优先使用 config.test_applicant_staff_no，不存在则使用真实值）
    person_record = detail.get("personApplyStatisticRecord", {})
    real_applicant_no = person_record.get("principalId", "") if person_record else ""
    # 优先使用配置中的测试申请人工号
    applicant_staff_no = config.get("test_applicant_staff_no") or real_applicant_no
    if applicant_staff_no:
        context["applicantStaffNo"] = applicant_staff_no
        context["applicantInfo"] = {
            "staffNo": applicant_staff_no,
            "company": person_record.get("company", "") if person_record else "",
            "deptName": person_record.get("deptName", "") if person_record else ""
        }

    if "currentApprovers" in status_detail:
        context["currentApprovers"] = status_detail["currentApprovers"]
    if "currentApproverStaffNo" in status_detail:
        context["currentApproverStaffNo"] = status_detail["currentApproverStaffNo"]
    if "columns" in detail:
        context["columns"] = detail["columns"]
    if "approveParams" in detail:
        context["approveParams"] = detail["approveParams"]
    if "applySubjectInfo" in detail:
        context["applySubjectInfo"] = detail["applySubjectInfo"]
    if "useSubjectInfo" in detail:
        context["useSubjectInfo"] = detail["useSubjectInfo"]
    if "tableInfo" in detail:
        context["tableInfo"] = detail["tableInfo"]
    if "applyScene" in detail:
        context["applyScene"] = detail["applyScene"]
    if "applyReason" in detail:
        context["applyReason"] = detail["applyReason"]

    approve_params = detail.get("approveParams", {})
    where_condition = approve_params.get("whereCondition", "")
    filter_condition = approve_params.get("filterCondition", {})
    if not where_condition and filter_condition:
        context["filterCondition"] = filter_condition

    apply_dept = extract_department(approve_params.get("executeProjectOwnerInfo", ""))
    data_owner_dept = extract_department(approve_params.get("projectOwnerInfo", ""))
    context["_apply_department"] = apply_dept
    context["_data_owner_department"] = data_owner_dept

    business_lines = []
    if extra_data and isinstance(extra_data, dict):
        bl = extra_data.get("business_lines") or extra_data.get("businessLines") or []
        if isinstance(bl, str):
            bl = [s.strip() for s in bl.split(",") if s.strip()]
        business_lines = list(bl)
    if not business_lines and config:
        business_lines = config.get("dgd_business_lines", []) or []
    context["_dgd_business_lines"] = business_lines

    return context


def _default_config_path():
    """基于脚本位置计算 pack 内默认 config 路径（绝对路径）。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', '..', 'config', 'config.json')


def parse_workflow_args():
    """兼容 workflow engine --workflow-engine-args-json 和直接命令行两种调用方式。"""
    _default_cfg = _default_config_path()
    parser = argparse.ArgumentParser(description="ODPS 审批 - 获取申请信息（workflow action）")
    # workflow engine 协议参数
    parser.add_argument("--workflow-engine-action", default=None)
    parser.add_argument("--workflow-engine-args-json", default=None)
    parser.add_argument("--workflow-engine-context-json", default=None)
    # 兼容直接命令行调用
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--application-id", default=None, help="审批单 ID")
    parser.add_argument("--task-content", default="", help="任务管理组件推送的 content JSON")
    args = parser.parse_args()

    if args.workflow_engine_args_json is not None:
        # workflow engine 调用：从 JSON 中提取参数
        action_args = json.loads(args.workflow_engine_args_json)
        context = json.loads(args.workflow_engine_context_json or "{}")
        config_path = action_args.get("config") or context.get("config") or _default_cfg
        return {
            "config": config_path,
            "application_id": action_args.get("application-id") or action_args.get("applicationId"),
            "task_content": action_args.get("task-content") or action_args.get("taskContent", ""),
        }
    else:
        # 直接命令行调用
        return {
            "config": args.config or _default_cfg,
            "application_id": args.application_id,
            "task_content": args.task_content,
        }


def main():
    action_args = parse_workflow_args()
    config = load_config(action_args["config"])
    application_id = action_args["application_id"]

    # Step 0: 任务归属校验
    is_assigned, assign_reason, extra_data = verify_assignee(action_args.get("task_content", ""), config)
    if not is_assigned:
        output = {
            "applicationId": application_id,
            "skipped": True,
            "reason": f"task_assignee_mismatch: {assign_reason}",
            "skip_stage": "step0_verify_assignee"
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(2)

    # Step 1: 前置校验
    can_proceed, reason, status_detail = verify_approver(application_id, config)
    if not can_proceed:
        # ⚠️ 不再 sys.exit(0) 吞掉错误！统一走 validate_context 做 BLOCK 校验
        output = {
            "applicationId": application_id,
            "skipped": True,
            "reason": reason
        }
        errors = validate_context(output)
        output["validation_errors"] = errors
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)  # 非零退出码 → workflow engine 标记节点失败

    # Step 2: 获取完整详情
    detail = fetch_full_detail(application_id, config)
    if detail is None:
        output = {
            "applicationId": application_id,
            "skipped": True,
            "reason": "getApplicationFullDetail 调用失败"
        }
        errors = validate_context(output)
        output["validation_errors"] = errors
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # Step 3: 构建上下文
    context = build_context(detail, status_detail, extra_data=extra_data, config=config)

    output = {
        "applicationId": application_id,
        "skipped": False,
        "context": context
    }

    # 集成校验
    errors = validate_context(output)
    has_block = any(e["level"] == "BLOCK" for e in errors)
    if has_block:
        output["validation_errors"] = errors
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    if errors:
        output["validation_warnings"] = [e for e in errors if e["level"] == "WARN"]

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
