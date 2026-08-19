#!/usr/bin/env python3
"""
ODPS 审批执行脚本（workflow action 版本）
集成 pre_check.py 前置校验 + validate_execution.py 输出校验。

执行顺序（v2.1）：
0. pre_check → 验证审批单状态仍为 APPROVING + 审批人仍是本 DGD
1. saveAuthorizeJudgment (if needed) + saveAuthorizeSeparate (if needed)
2. updateWhereCondition (if needed)
3. deleteApplicationColumn (if needed)
4. advanceProcess (最后)

用法：
    python3 execute_approval.py --config <config_path> \
        --application-id <id> \
        --decision <agree|reject|assign|add_sign|close> \
        --operator-id <DGD工号> \
        [--authorize-judgment <JSON>] \
        [--authorize-separate <JSON>] \
        [--where-condition <SQL>] \
        [--delete-columns <col1,col2>] \
        [--assignee-id <工号>] \
        [--sign-list <工号1,工号2>] \
        [--reason <审批意见>]
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'config'))
try:
    from mcp_utils import load_config, run_mcp_call
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config'))
    from mcp_utils import load_config, run_mcp_call


# ── 前置校验（集成自 pre_check.py）──────────────────────────────

def pre_check(application_id, config):
    """前置状态校验：确认审批单状态仍为 APPROVING，且当前审批人仍是本 DGD。"""
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
        return False, f"审批单状态为 {approve_status}，非 APPROVING，可能已被处理或撤回", data

    current_approvers = data.get("currentApprovers", [])
    if isinstance(current_approvers, list):
        approver_ids = [a.get("staffNo", a.get("staff_no", "")) for a in current_approvers if isinstance(a, dict)]
        if staff_no not in approver_ids:
            return False, f"当前审批人不包含 {staff_no}，审批可能已流转到其他节点", data

    return True, None, data


# ── 输出校验（集成自 validate_execution.py）──────────────────────

def validate_execution(data: dict) -> list:
    """校验 execute_approval 输出。"""
    errors = []
    application_id = data.get("application_id", "")
    status = data.get("status", "")
    execution_log = data.get("execution_log", [])
    decision = data.get("decision", "")

    if not application_id:
        errors.append({"level": "BLOCK", "code": "MISSING_APPLICATION_ID", "msg": "执行结果缺少 application_id"})

    valid_statuses = {"success", "partial", "failed", "cancelled"}
    if status not in valid_statuses:
        errors.append({"level": "BLOCK", "code": "INVALID_STATUS", "msg": f"status={status} 不在合法值 {valid_statuses} 中"})

    if execution_log and isinstance(execution_log, list):
        advance_idx = None
        table_op_idx = None
        for i, log in enumerate(execution_log):
            op = log.get("operation", "") if isinstance(log, dict) else str(log)
            if "advanceProcess" in op or "advance" in op.lower():
                advance_idx = i
            if "updateWhereCondition" in op or "deleteApplicationColumn" in op:
                table_op_idx = i
        if advance_idx is not None and table_op_idx is not None and advance_idx < table_op_idx:
            errors.append({"level": "BLOCK", "code": "EXECUTION_ORDER_VIOLATION", "msg": "advanceProcess 在表操作之前执行，违反严格顺序约束"})

    if status == "success" and (not execution_log or len(execution_log) == 0):
        errors.append({"level": "BLOCK", "code": "SUCCESS_WITHOUT_LOG", "msg": "执行状态为 success 但 execution_log 为空"})

    if decision in ("agree", "approve_with_conditions") and execution_log:
        has_advance = any(
            "advanceProcess" in (log.get("operation", "") if isinstance(log, dict) else str(log))
            for log in execution_log
        )
        if not has_advance:
            errors.append({"level": "BLOCK", "code": "MISSING_ADVANCE_PROCESS", "msg": f"决策为 {decision} 但未执行 advanceProcess"})

    if status == "partial":
        failed_ops = [
            log.get("operation", "unknown") if isinstance(log, dict) else str(log)
            for log in execution_log
            if isinstance(log, dict) and log.get("status") == "failed"
        ]
        if not failed_ops:
            errors.append({"level": "WARN", "code": "PARTIAL_WITHOUT_FAILED_DETAIL", "msg": "执行状态为 partial 但未标明哪些步骤失败"})

    return errors


# ── 执行逻辑 ────────────────────────────────────────────────────

def execute_save_authorize_judgment(application_id, judgment, operator_id, config):
    """Step 1a: 保存确权判断。"""
    args = {
        "applicationId": int(application_id) if str(application_id).isdigit() else application_id,
        "operatorId": operator_id,
    }
    if "containPersonalInfo" in judgment:
        args["isContainPersonalInfo"] = bool(judgment["containPersonalInfo"])
    if "needAuthorize" in judgment:
        args["isNeedAuthorize"] = bool(judgment["needAuthorize"])
    if judgment.get("authAssetCode"):
        args["authAssetCode"] = judgment["authAssetCode"]
    if judgment.get("authorizeScope"):
        args["authorizeScope"] = judgment["authorizeScope"]
    if judgment.get("authorizeRemarkInfo"):
        args["authorizeRemarkInfo"] = judgment["authorizeRemarkInfo"]
    if judgment.get("dataProcessingRelation"):
        args["dataProcessingRelation"] = judgment["dataProcessingRelation"]

    response = run_mcp_call(config["mcp_server_ds"], "saveAuthorizeJudgment", args, config)
    if response and (response.get("success") or "data" in response):
        return {"success": True, "step": "saveAuthorizeJudgment", "args": args}
    error_msg = response.get("message", "未知错误") if response else "MCP 调用失败"
    return {"success": False, "step": "saveAuthorizeJudgment", "error": error_msg, "args": args}


def execute_save_authorize_separate(application_id, separate_cfg, operator_id, config):
    """Step 1b: 保存确权配置。"""
    args = {
        "applicationId": int(application_id) if str(application_id).isdigit() else application_id,
        "operatorId": operator_id,
    }
    for key in ["sceneName", "sceneCode", "authorizeTableGuid", "originalTableGuid",
                "originalSubjectCode", "originalSubjectName",
                "targetSubjectCode", "targetSubjectName",
                "viewGuid", "viewDesc", "partitionType"]:
        if separate_cfg.get(key):
            args[key] = separate_cfg[key]
    if separate_cfg.get("relationKeys"):
        args["relationKeys"] = separate_cfg["relationKeys"]
    if separate_cfg.get("partitionColumns"):
        args["partitionColumns"] = separate_cfg["partitionColumns"]

    response = run_mcp_call(config["mcp_server_ds"], "saveAuthorizeSeparate", args, config)
    if response and (response.get("success") or "data" in response):
        return {"success": True, "step": "saveAuthorizeSeparate", "args": args}
    error_msg = response.get("message", "未知错误") if response else "MCP 调用失败"
    return {"success": False, "step": "saveAuthorizeSeparate", "error": error_msg, "args": args}


def execute_update_where_condition(application_id, where_condition, operator_id, config):
    """Step 2: 行级过滤。"""
    args = {
        "applicationId": int(application_id) if str(application_id).isdigit() else application_id,
        "whereCondition": where_condition,
        "operatorId": operator_id
    }
    response = run_mcp_call(config["mcp_server_ds"], "updateWhereCondition", args, config)
    if response and (response.get("success") or "data" in response):
        return {"success": True, "step": "updateWhereCondition", "condition": where_condition}
    error_msg = response.get("message", "未知错误") if response else "MCP 调用失败"
    return {"success": False, "step": "updateWhereCondition", "error": error_msg}


def execute_delete_application_column(application_id, column_names, operator_id, config):
    """Step 3: 删除字段。"""
    args = {
        "applicationId": int(application_id) if str(application_id).isdigit() else application_id,
        "columnNames": column_names,
        "operatorId": operator_id
    }
    response = run_mcp_call(config["mcp_server_ds"], "deleteApplicationColumn", args, config)
    if response and (response.get("success") or "data" in response):
        return {"success": True, "step": "deleteApplicationColumn", "columns": column_names}
    error_msg = response.get("message", "未知错误") if response else "MCP 调用失败"
    return {"success": False, "step": "deleteApplicationColumn", "error": error_msg}


def execute_advance_process(application_id, decision, operator_id, reason, assignee_id, config,
                             sign_list=None):
    """Step 4: 推进审批流（最后执行）。"""
    app_id = int(application_id) if str(application_id).isdigit() else application_id

    if decision in ("approve", "agree", "approve_with_conditions"):
        action = "PUSH"
        mcp_args = {"applicationId": app_id, "action": action, "operatorId": operator_id,
                    "reason": reason or "AI 辅助审批通过"}
    elif decision == "reject":
        action = "PUSH"
        mcp_args = {"applicationId": app_id, "action": action, "operatorId": operator_id,
                    "reason": reason or "AI 辅助审批拒绝"}
    elif decision == "assign":
        if not assignee_id:
            return {"success": False, "step": "advanceProcess", "error": "转交操作缺少 assigneeId"}
        action = "ASSIGN"
        mcp_args = {"applicationId": app_id, "action": action, "operatorId": operator_id,
                    "assigneeId": assignee_id, "reason": reason or "转交其他审批人处理"}
    elif decision == "add_sign":
        if not sign_list:
            return {"success": False, "step": "advanceProcess", "error": "加签操作缺少 signList"}
        action = "SIGN"
        mcp_args = {"applicationId": app_id, "action": action, "operatorId": operator_id,
                    "signList": sign_list, "reason": reason or "加签领域专家审批"}
    elif decision == "close":
        action = "CLOSE"
        if not reason:
            return {"success": False, "step": "advanceProcess", "error": "关闭操作必须附带 reason（关闭原因）"}
        mcp_args = {"applicationId": app_id, "action": action, "operatorId": operator_id, "reason": reason}
    else:
        return {"success": False, "step": "advanceProcess", "error": f"未知决策类型: {decision}"}

    response = run_mcp_call(config["mcp_server_ds"], "advanceProcess", mcp_args, config)
    if response and (response.get("success") or "data" in response):
        return {"success": True, "step": "advanceProcess", "action": action, "decision": decision}
    error_msg = response.get("message", "未知错误") if response else "MCP 调用失败"
    return {"success": False, "step": "advanceProcess", "error": error_msg}


def parse_json_arg(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {value[:200]}, error={e}", file=sys.stderr)
        return None


def _default_config_path():
    """基于脚本位置计算 pack 内默认 config 路径（绝对路径）。"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', '..', 'config', 'config.json')


def parse_workflow_args():
    """兼容 workflow engine --workflow-engine-args-json 和直接命令行两种调用方式。"""
    _default_cfg = _default_config_path()
    parser = argparse.ArgumentParser(description="ODPS 审批执行脚本（workflow action）")
    # workflow engine 协议参数
    parser.add_argument("--workflow-engine-action", default=None)
    parser.add_argument("--workflow-engine-args-json", default=None)
    parser.add_argument("--workflow-engine-context-json", default=None)
    # 兼容直接命令行调用
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--application-id", default=None, help="审批单ID")
    parser.add_argument("--decision", default=None,
                        choices=["approve", "agree", "approve_with_conditions", "reject", "assign", "add_sign", "close"],
                        help="审批决策")
    parser.add_argument("--operator-id", default=None, help="操作人工号(DGD工号)")
    parser.add_argument("--authorize-judgment", default="", help="确权判断 JSON")
    parser.add_argument("--authorize-separate", default="", help="确权配置 JSON")
    parser.add_argument("--where-condition", default="", help="行级过滤条件 SQL")
    parser.add_argument("--delete-columns", default="", help="待删除字段列表，逗号分隔")
    parser.add_argument("--assignee-id", default="", help="转交目标工号(assign 时必填)")
    parser.add_argument("--sign-list", default="", help="加签人工号列表，逗号分隔(add_sign 时必填)")
    parser.add_argument("--reason", default="", help="审批意见")
    args = parser.parse_args()

    if args.workflow_engine_args_json is not None:
        # workflow engine 调用：从 JSON 中提取参数
        action_args = json.loads(args.workflow_engine_args_json)
        context = json.loads(args.workflow_engine_context_json or "{}")
        config_path = action_args.get("config") or context.get("config") or _default_cfg
        return {
            "config": config_path,
            "application_id": action_args.get("application-id") or action_args.get("applicationId"),
            "decision": action_args.get("decision"),
            "operator_id": action_args.get("operator-id") or action_args.get("operatorId"),
            "authorize_judgment": action_args.get("authorize-judgment") or action_args.get("authorizeJudgment", ""),
            "authorize_separate": action_args.get("authorize-separate") or action_args.get("authorizeSeparate", ""),
            "where_condition": action_args.get("where-condition") or action_args.get("whereCondition", ""),
            "delete_columns": action_args.get("delete-columns") or action_args.get("deleteColumns", ""),
            "assignee_id": action_args.get("assignee-id") or action_args.get("assigneeId", ""),
            "sign_list": action_args.get("sign-list") or action_args.get("signList", ""),
            "reason": action_args.get("reason", ""),
        }
    else:
        # 直接命令行调用
        return {
            "config": args.config or _default_cfg,
            "application_id": args.application_id,
            "decision": args.decision,
            "operator_id": args.operator_id,
            "authorize_judgment": args.authorize_judgment,
            "authorize_separate": args.authorize_separate,
            "where_condition": args.where_condition,
            "delete_columns": args.delete_columns,
            "assignee_id": args.assignee_id,
            "sign_list": args.sign_list,
            "reason": args.reason,
        }


def main():
    action_args = parse_workflow_args()
    config = load_config(action_args["config"])
    application_id = action_args["application_id"]
    operator_id = action_args["operator_id"]
    decision = action_args["decision"]
    is_approve_like = decision in ("approve", "agree", "approve_with_conditions")

    # Step 0: 前置状态校验
    can_proceed, pre_check_reason, pre_check_detail = pre_check(application_id, config)
    if not can_proceed:
        output = {
            "application_id": application_id,
            "decision": decision,
            "operator_id": operator_id,
            "aborted": True,
            "abort_reason": f"前置校验失败: {pre_check_reason}",
            "execution_log": [],
            "status": "cancelled",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine_version": "v2.1"
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    execution_log = []
    aborted = False
    abort_reason = ""

    authorize_judgment = parse_json_arg(action_args["authorize_judgment"])
    authorize_separate = parse_json_arg(action_args["authorize_separate"])

    # Step 1a: 保存确权判断
    if is_approve_like and authorize_judgment:
        result = execute_save_authorize_judgment(application_id, authorize_judgment, operator_id, config)
        execution_log.append(result)
        if not result["success"]:
            aborted = True
            abort_reason = f"saveAuthorizeJudgment 失败: {result.get('error')}"

    # Step 1b: 保存确权配置
    if not aborted and is_approve_like and authorize_separate:
        result = execute_save_authorize_separate(application_id, authorize_separate, operator_id, config)
        execution_log.append(result)
        if not result["success"]:
            aborted = True
            abort_reason = f"saveAuthorizeSeparate 失败: {result.get('error')}（确权判断已写入但配置失败）"

    # Step 2: 行级过滤
    if not aborted and is_approve_like and action_args["where_condition"]:
        result = execute_update_where_condition(application_id, action_args["where_condition"], operator_id, config)
        execution_log.append(result)
        if not result["success"]:
            aborted = True
            abort_reason = f"updateWhereCondition 失败: {result.get('error')}"

    # Step 3: 删除字段
    if not aborted and is_approve_like and action_args["delete_columns"]:
        column_names = [c.strip() for c in action_args["delete_columns"].split(",") if c.strip()]
        if column_names:
            result = execute_delete_application_column(application_id, column_names, operator_id, config)
            execution_log.append(result)
            if not result["success"]:
                aborted = True
                abort_reason = f"deleteApplicationColumn 失败: {result.get('error')}"

    # Step 4: 推进审批流
    if not aborted:
        sign_list = [s.strip() for s in action_args["sign_list"].split(",") if s.strip()] if action_args["sign_list"] else None
        result = execute_advance_process(application_id, decision, operator_id,
                                          action_args["reason"], action_args["assignee_id"], config,
                                          sign_list=sign_list)
        execution_log.append(result)
        if not result["success"]:
            abort_reason = f"advanceProcess 失败: {result.get('error')}（表操作可能已执行但审批流未推进）"

    output = {
        "application_id": application_id,
        "decision": decision,
        "operator_id": operator_id,
        "aborted": aborted,
        "abort_reason": abort_reason if aborted else None,
        "execution_log": execution_log,
        "status": "partial" if aborted else "success",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "engine_version": "v2.1"
    }

    # 集成校验
    errors = validate_execution(output)
    has_block = any(e["level"] == "BLOCK" for e in errors)
    if has_block:
        output["validation_errors"] = errors
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    if errors:
        output["validation_warnings"] = [e for e in errors if e["level"] == "WARN"]

    print(json.dumps(output, ensure_ascii=False))
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
