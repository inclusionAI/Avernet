#!/usr/bin/env python3
"""
ODPS 审批 - 保存风险分析结果到 workflowData（workflow action）
用于 risk_analysis 节点的 onSuccess hook，将 scenario_verdict 写入 workflowData.triggered_by。

用法：
    python3 save_risk_verdict.py --scenario-verdict <verdict>
"""

import argparse
import json
import sys


def parse_workflow_args():
    """兼容 workflow engine --workflow-engine-args-json 和直接命令行两种调用方式。"""
    parser = argparse.ArgumentParser(description="保存风险分析结果到 workflowData")
    # workflow engine 协议参数
    parser.add_argument("--workflow-engine-action", default=None)
    parser.add_argument("--workflow-engine-args-json", default=None)
    parser.add_argument("--workflow-engine-context-json", default=None)
    # 兼容直接命令行调用
    parser.add_argument("--scenario-verdict", default=None, help="场景引擎判决结果")
    args = parser.parse_args()

    if args.workflow_engine_args_json is not None:
        # workflow engine 调用：从 JSON 中提取参数
        action_args = json.loads(args.workflow_engine_args_json)
        return {
            "scenario_verdict": action_args.get("scenario-verdict") or action_args.get("scenarioVerdict", ""),
        }
    else:
        # 直接命令行调用
        return {
            "scenario_verdict": args.scenario_verdict,
        }


def main():
    action_args = parse_workflow_args()
    verdict = action_args["scenario_verdict"]
    triggered_by = "scenario_supplement" if verdict == "supplement" else ""

    output = {
        "scenario_verdict": verdict,
        "triggered_by": triggered_by
    }

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
