#!/usr/bin/env python3
"""Governance 反馈回路：从 ClawWeb 驳回项中学习，更新 fix-patterns.md。

用法:
  python3 learn_from_rejects.py --output-fix-patterns references/fix-patterns.md
  python3 learn_from_rejects.py --output-fix-patterns references/fix-patterns.md --apply
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


# ── 驳回分类 ────────────────────────────────────────────────────
# 优先级: 有修复方案 > 明确拒绝(AUTO→MANUAL) > 明确误判(DROP)

REJECT_CATEGORIES = {
    "HAS_FIX_PLAN": {
        "keywords": ["写入TOOLS.md", "写入 AGENTS.md", "使用.*技能", "替换为.*mcp", "修正.*配置",
                      "添加.*skill", "更新.*配置", "修改.*参数", "正确.*方式", "应该是自动",
                      "可以自动", "需要将.*写入", "补充.*skill"],
        "action": "UPGRADE_TO_AUTO",
        "desc": "Admin 提供了明确的修复方案 → 升级为 AUTO 并新增 fix-pattern"
    },
    "SHOULD_BE_MANUAL": {
        "keywords": ["无法自动", "环境不支持", "当前做不到", "不可自动", "需要人工操作",
                      "人力介入", "手动修复", "暂时无法", "不能自动", "沙箱.*销毁",
                      "需要管理员", "需要权限"],
        "action": "DOWNGRADE_TO_MANUAL",
        "desc": "原判 AUTO 但环境做不到 → 降级为 MANUAL"
    },
    "EXPLICIT_REJECT": {
        "keywords": ["驳回测试", "^测试$", "测试驳回", "不是问题", "误判", "false positive",
                      "已恢复", "无需修复", "正常现象", "不需要修"],
        "action": "ADD_DROP_PATTERN",
        "desc": "明确表示不是真正的整改项 → 添加 DROP 模式"
    },
}


def classify_reject(reason: str, original_action_type: str) -> Optional[Tuple[str, str]]:
    """根据驳回理由和原修复方式分类。优先匹配有修复方案的。"""
    if not reason or len(reason.strip()) < 5:
        return None  # 空白/过短驳回，跳过
    reason_lower = reason.lower()

    # 1. 先检查是否有修复方案（最高优先级）
    import re as _re
    for kw in REJECT_CATEGORIES["HAS_FIX_PLAN"]["keywords"]:
        if _re.search(kw.lower(), reason_lower):
            cat = "HAS_FIX_PLAN"
            return (cat, REJECT_CATEGORIES[cat]["action"])

    # 2. 检查是否明确表示只能手动
    for kw in REJECT_CATEGORIES["SHOULD_BE_MANUAL"]["keywords"]:
        if _re.search(kw.lower(), reason_lower):
            cat = "SHOULD_BE_MANUAL"
            return (cat, REJECT_CATEGORIES[cat]["action"])

    # 3. 检查是否明确驳回
    for kw in REJECT_CATEGORIES["EXPLICIT_REJECT"]["keywords"]:
        if _re.search(kw.lower(), reason_lower):
            cat = "EXPLICIT_REJECT"
            return (cat, REJECT_CATEGORIES[cat]["action"])

    # 4. 兜底：按原 actionType 推断
    if original_action_type == "DIRECT_EVOLUTION":
        return ("SHOULD_BE_MANUAL", "DOWNGRADE_TO_MANUAL")
    return None


# ── ClawWeb API ──────────────────────────────────────────────────


def query_rejected(base_url: str, since_days: int = 30) -> List[dict]:
    """查询最近被驳回的改进项。"""
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%dT00:00:00+08:00")
    params = f"?adminReviewStatus=REJECTED&since={since}&limit=100"
    fields = "&fields=improvementId,ownerUserId,botId,title,actionType,rootCauseSummary,adminReviewReason,sourceRuleId,adminReviewedAt"
    url = base_url.rstrip("/") + "/api/insight/v1/internal/governance/actions" + params + fields
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("items", [])
    except urllib.error.HTTPError as e:
        print(f"[API] 查询驳回项失败 HTTP {e.code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[API] 请求失败: {e}", file=sys.stderr)
        return []


# ── fix-patterns.md 读写 ─────────────────────────────────────────


def load_fix_patterns(path: str) -> str:
    with open(path) as f:
        return f.read()


def append_pattern(content: str, pattern: str) -> str:
    """在 fix-patterns.md 末尾追加一条模式。"""
    # 避免重复：检查模式编号是否已存在
    if f"### {pattern.split(chr(10))[0].strip('# ')}" in content or pattern.strip() in content:
        return content
    return content.rstrip() + "\n\n" + pattern.strip() + "\n"


# ── 学习逻辑 ────────────────────────────────────────────────────


def _extract_failure_class(root_cause: str) -> str:
    """从 rootCauseSummary 提取 failure_class。"""
    for fc in ["PERMISSION_NETWORK", "TOOL_FAILURE", "WORKFLOW_FAILURE", "CONFIG_MISSING",
               "DATA_ISSUE", "OUTPUT_WRONG", "PARAMETER_ERROR", "CAPABILITY_BOUNDARY"]:
        if fc in (root_cause or ""):
            return fc
    return "待补充"

def _extract_component(root_cause: str) -> str:
    """从 rootCauseSummary 提取组件类型。"""
    if not root_cause:
        return "待补充"
    if "skill" in root_cause.lower() or "SKILL" in root_cause:
        return "skill"
    if "mcp" in root_cause.lower() or "MCP" in root_cause:
        return "mcp"
    if "工具" in root_cause:
        return "tool"
    return "待补充"


def build_learned_pattern(reject_item: dict, category: str) -> Optional[str]:
    """根据驳回项构建新的 fix-pattern。"""
    title = reject_item.get("title", "")
    root_cause = reject_item.get("rootCauseSummary", "")
    reason = reject_item.get("adminReviewReason", "")
    fc = _extract_failure_class(root_cause)
    comp = _extract_component(root_cause)

    if category == "HAS_FIX_PLAN":
        # Admin 给了明确的修复方案 → UPGRADE_TO_AUTO
        return f"""### [从驳回学习-升级] {title[:60]}
| 字段 | 值 |
|------|------|
| **failure_class** | {fc} |
| **component_type** | {comp} |
| **匹配特征** | {reason[:120]} |
| **action_type** | DIRECT_EVOLUTION（Admin 驳回时给出自动修复方案） |
| **修复模板** | {reason} |
| **验证方式** | 触发同类任务，确认不再出现原错误 |
| **来源** | 驳回项 #{reject_item['improvementId']}, {reject_item.get('adminReviewedAt','')} |
"""
    elif category == "EXPLICIT_REJECT":
        return f"""### [从驳回学习] {title[:60]}
| 字段 | 值 |
|------|------|
| **failure_class** | {fc} |
| **匹配特征** | {reason[:120]} |
| **action_type** | DROP |
| **来源** | 驳回项 #{reject_item['improvementId']}, 驳回理由: {reason[:100]} |
"""
    elif category == "SHOULD_BE_MANUAL":
        return f"""### [从驳回学习-降级] {title[:60]}
| 字段 | 值 |
|------|------|
| **failure_class** | {fc} |
| **匹配特征** | {reason[:120]} |
| **action_type** | ASSIGN_OWNER（由 AUTO 降级） |
| **来源** | 驳回项 #{reject_item['improvementId']}, 驳回理由: {reason[:100]} |
"""
    return None


# ── 候选数量不足 → 放宽条件 ─────────────────────────────────────


def check_candidate_threshold(candidates_json_path: str, min_high: int = 3) -> bool:
    """检查本次召回是否足够。不足返回 False。"""
    if not os.path.exists(candidates_json_path):
        return False
    with open(candidates_json_path) as f:
        data = json.load(f)
    high = [c for c in data if c.get("confidence_level") == "HIGH"]
    return len(high) >= min_high


def build_relaxed_sql_template() -> str:
    """返回放宽条件的 SQL 提示词（7 天窗口 + 无 confidence 阈值限制）。"""
    return """
-- 放宽条件查询:
--   1. 窗口从 14 天 → 7 天 (聚焦最近一周)
--   2. 不再只取 HIGH, 取所有 capability_fail_task_count >= 3 的候选
--   3. 扩大失败类范围, 加入 AWAITING_USER / ASYNC_PENDING 等边缘分类
SELECT user_id, bot_id, ...
WHERE dt >= '{start_dt}' AND dt <= '{end_dt}'
  AND llm_task_failure_classes IS NOT NULL
-- 不限制 confidence_level, 手动筛选
ORDER BY capability_fail_task_count DESC
LIMIT 200
"""


# ── Main ─────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Governance 反馈回路")
    parser.add_argument("--output-fix-patterns", default=None, help="fix-patterns.md 路径")
    parser.add_argument("--base-url", default=None, help="ClawWeb URL")
    parser.add_argument("--apply", action="store_true", help="实际写入（否则 dry-run）")
    parser.add_argument("--since-days", type=int, default=30, help="查询多少天内的驳回项")
    parser.add_argument("--candidates", default=None, help="本次 candidates.json，用于检查候选数量")
    args = parser.parse_args()

    base_url = args.base_url or os.environ.get("CLAWWEB_URL", "https://clawweb.alipay.com")

    # 1. 查询驳回项
    print("=" * 60, file=sys.stderr)
    print("Governance 反馈回路", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    rejected = query_rejected(base_url, args.since_days)
    if not rejected:
        print("[反馈] 无驳回项，无需学习", file=sys.stderr)
    else:
        print(f"[反馈] 查询到 {len(rejected)} 条驳回项", file=sys.stderr)
        suggestions = []
        for item in rejected:
            reason = item.get("adminReviewReason", "")
            action_type = item.get("actionType", "")
            cat_result = classify_reject(reason, action_type)
            if cat_result:
                cat, action = cat_result
                print(f"  #{item['improvementId']}: {cat} — {reason[:80]}", file=sys.stderr)
                pattern = build_learned_pattern(item, cat)
                if pattern:
                    suggestions.append((item, cat, pattern))

        if suggestions and args.output_fix_patterns:
            if os.path.exists(args.output_fix_patterns):
                content = load_fix_patterns(args.output_fix_patterns)
                for item, cat, pattern in suggestions:
                    content = append_pattern(content, pattern)
                if args.apply:
                    with open(args.output_fix_patterns, "w") as f:
                        f.write(content)
                    print(f"[应用] 已更新 {args.output_fix_patterns} ({len(suggestions)} 条新模式)", file=sys.stderr)
                else:
                    print(f"[dry-run] 将向 {args.output_fix_patterns} 追加 {len(suggestions)} 条模式，加 --apply 执行", file=sys.stderr)
                    for item, cat, pattern in suggestions:
                        print(f"\n  --- 建议追加 (ID={item['improvementId']}, {cat}) ---")
                        print(pattern[:300])
            else:
                print(f"[跳过] {args.output_fix_patterns} 不存在", file=sys.stderr)

    # 2. 候选数量检查
    if args.candidates:
        if check_candidate_threshold(args.candidates):
            print(f"\n[候选] HIGH 候选 ≥3，正常", file=sys.stderr)
        else:
            print(f"\n[候选] HIGH 候选不足 (<3)，建议放宽条件:", file=sys.stderr)
            print("  1. 缩短窗口到 7 天 (--window-days 7)", file=sys.stderr)
            print("  2. 扩大范围取 MEDIUM + HIGH", file=sys.stderr)
            print("  3. 放宽 Confidence 阈值 (capability_fail_task_count >= 3)", file=sys.stderr)

    print("\n[完成]", file=sys.stderr)


if __name__ == "__main__":
    main()