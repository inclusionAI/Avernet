"""
BCS CLI

G1: Fusion Entry Layer / G2: Conflict Alignment Layer / G5: Expert Diagnosis Layer

BCS 命令行工具，提供 fuse 子命令。

命令：
- bcs-cli fuse: 发起融合操作

约束：
- CLI 直接调用 GroupFusionService，不经过 HTTP
- CLI 与 HTTP 共用同一套 FusionRequest/FusionResult

LLM 增强：
- 设置环境变量 LLM_ENABLED=true 启用 LLM recommendation
- 需要同时配置 LLM_BASE_URL 和 LLM_AUTH_TOKEN

G1 示例:
bcs-cli fuse \
  --group grp-001 \
  --question "这个方案从各角度是否可行" \
  --participants "张三,DBA,安全"

G2 示例:
bcs-cli fuse \
  --group grp-fusion-001 \
  --question "如何协调代码与PRD的超时时间冲突？" \
  --participants "zhangsan,lisi,anquan" \
  --mode conflict_alignment \
  --pretty

G5 示例:
bcs-cli fuse \
  --group grp-expert-001 \
  --question "这个方案是否可以上线？" \
  --participants "anquan,fawu,dba" \
  --mode expert_diagnosis \
  --pretty
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from enum import IntEnum
from typing import Literal, Optional, Sequence

from src.domain.models.fusion_request import FusionRequest, FuseOptions, FuseMetadata
from src.domain.models.fusion_result import FusionResult
from src.application.services.group_fusion_service import GroupFusionService
from src.domain.services.perspective_provider import PerspectiveProvider


class ExitCode(IntEnum):
    """退出码"""
    SUCCESS = 0
    INVALID_ARGUMENT = 1
    SERVICE_ERROR = 2
    UNKNOWN_ERROR = 5


def parse_args(args: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """
    解析命令行参数

    Args:
        args: 命令行参数，如果为 None 则从 sys.argv 获取

    Returns:
        解析后的参数
    """
    parser = argparse.ArgumentParser(
        prog="bcs-cli",
        description="BCS 协作控制层命令行工具",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # fuse 子命令
    fuse_parser = subparsers.add_parser(
        "fuse",
        help="发起融合操作",
        description="在已存在的 group 上发起多参与者视角融合",
    )

    # 必填参数
    fuse_parser.add_argument(
        "--group",
        required=True,
        help="已存在的 group/session ID",
    )

    fuse_parser.add_argument(
        "--question",
        required=True,
        help="需要多方协作评估的问题",
    )

    fuse_parser.add_argument(
        "--participants",
        required=True,
        help="参与者列表，逗号分隔",
    )

    # 可选参数
    fuse_parser.add_argument(
        "--driver",
        default=None,
        help="显式指定 driver bot ID",
    )

    fuse_parser.add_argument(
        "--timeout",
        type=int,
        default=15000,
        help="超时时间（毫秒），默认 15000",
    )

    fuse_parser.add_argument(
        "--parallel",
        action="store_true",
        default=True,
        help="并行收集视角（默认启用）",
    )

    fuse_parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="顺序收集视角",
    )

    fuse_parser.add_argument(
        "--strict-participants",
        action="store_true",
        default=True,
        help="participant 解析失败时硬失败（默认启用）",
    )

    fuse_parser.add_argument(
        "--no-strict-participants",
        action="store_true",
        help="participant 解析失败时继续执行",
    )

    fuse_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="输出原始 JSON",
    )

    fuse_parser.add_argument(
        "--pretty",
        action="store_true",
        help="格式化输出，适合人读",
    )

    fuse_parser.add_argument(
        "--request-id",
        default=None,
        help="显式 request_id，便于追踪",
    )

    # G2/G5: fusion_mode 参数
    fuse_parser.add_argument(
        "--mode",
        dest="fusion_mode",
        choices=["agent", "conflict_alignment", "expert_diagnosis"],
        default="agent",
        help="融合模式：agent（G1，默认）、conflict_alignment（G2）或 expert_diagnosis（G5）",
    )

    return parser.parse_args(args)


def _create_llm_recommendation_service():
    """
    创建 LLM Recommendation Service（如果环境配置正确）

    Returns:
        FusionRecommendationService 或 None
    """
    # 检查是否启用 LLM
    llm_enabled = os.environ.get("LLM_ENABLED", "").lower() == "true"

    if not llm_enabled:
        return None

    # 检查必要的环境变量
    base_url = os.environ.get("LLM_BASE_URL")
    auth_token = os.environ.get("LLM_AUTH_TOKEN")

    if not base_url or not auth_token:
        return None

    # 创建 LLM 服务链
    try:
        from src.infra.llm.config.llm_settings import LLMSettings
        from src.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
        from src.infra.llm.routing.static_llm_router import StaticLLMRouter
        from src.application.services.llm_gateway_service import LLMGatewayService
        from src.application.services.fusion_recommendation_service import FusionRecommendationService

        settings = LLMSettings()
        provider = AnthropicCompatibleProvider(settings=settings)
        router = StaticLLMRouter(settings=settings)
        gateway = LLMGatewayService(provider=provider, router=router)
        return FusionRecommendationService(gateway=gateway)
    except Exception:
        # LLM 服务创建失败，回退到规则方法
        return None


def get_default_service() -> GroupFusionService:
    """获取默认服务实例"""
    from src.infra.providers.stub_perspective_provider import StubPerspectiveProvider
    provider = StubPerspectiveProvider()

    # 尝试创建 LLM recommendation service
    rec_service = _create_llm_recommendation_service()

    return GroupFusionService(
        provider=provider,
        recommendation_service=rec_service,
    )


def format_pretty_output(result: FusionResult) -> str:
    """
    格式化输出为可读文本

    Args:
        result: 融合结果

    Returns:
        格式化后的文本
    """
    lines = []

    lines.append(f"Group: {result.group_id}")
    lines.append(f"Fusion ID: {result.fusion_id}")
    lines.append(f"Mode: {result.fusion_mode}")
    lines.append(f"Question: {result.question}")

    if result.driver_bot_id:
        lines.append(f"Driver: {result.driver_bot_id}")

    lines.append("")

    # Perspectives
    lines.append("Perspectives")
    lines.append("-" * 40)
    for p in result.perspectives:
        status_icon = "✓" if p.status == "completed" else "✗"
        lines.append(f"[{status_icon}] {p.participant_id}: {p.summary}")
        # G2: 显示 key_points 和 concerns
        if p.key_points:
            lines.append(f"    Key Points: {', '.join(p.key_points)}")
        if p.concerns:
            lines.append(f"    Concerns: {', '.join(p.concerns)}")

    lines.append("")

    # G2: Conflicts
    if result.fusion_mode == "conflict_alignment" and result.conflicts:
        lines.append("Conflicts")
        lines.append("-" * 40)
        for c in result.conflicts:
            lines.append(f"  [{c.severity.upper()}] {c.issue}")
            lines.append(f"    Parties: {', '.join(c.parties)}")
            for pos in c.positions:
                lines.append(f"    - {pos}")
        lines.append("")

    # G2: Alignment Points
    if result.fusion_mode == "conflict_alignment" and result.alignment_points:
        lines.append("Alignment Points")
        lines.append("-" * 40)
        for a in result.alignment_points:
            lines.append(f"  • {a.summary}")
            if a.participants:
                lines.append(f"    Participants: {', '.join(a.participants)}")
        lines.append("")

    # G2: Key Insights
    if result.fusion_mode == "conflict_alignment" and result.key_insights:
        lines.append("Key Insights")
        lines.append("-" * 40)
        for insight in result.key_insights:
            lines.append(f"  • {insight}")
        lines.append("")

    # G5: Risk Assessment
    if result.fusion_mode == "expert_diagnosis" and result.risk_assessment:
        lines.append("Risk Assessment")
        lines.append("-" * 40)
        lines.append(f"  Overall: {result.risk_assessment.overall.value.upper()}")
        if result.risk_assessment.categories:
            lines.append("  Categories:")
            for domain, level in result.risk_assessment.categories.items():
                lines.append(f"    • {domain}: {level.value.upper()}")
        lines.append("")

    # G5: Critical Issues
    if result.fusion_mode == "expert_diagnosis" and result.critical_issues:
        lines.append("Critical Issues")
        lines.append("-" * 40)
        for issue in result.critical_issues:
            lines.append(f"  [{issue.severity.value.upper()}] {issue.issue}")
            lines.append(f"    Domain: {issue.domain}")
            lines.append(f"    Source: {issue.source}")
        lines.append("")

    # G5: Expert Recommendations
    if result.fusion_mode == "expert_diagnosis" and result.recommendations:
        lines.append("Expert Recommendations")
        lines.append("-" * 40)
        for rec in result.recommendations:
            lines.append(f"  [{rec.priority.value}] {rec.action}")
            if rec.owner:
                lines.append(f"    Owner: {rec.owner}")
            if rec.domain:
                lines.append(f"    Domain: {rec.domain}")
        lines.append("")

    # G5: Go-Live Conditions
    if result.fusion_mode == "expert_diagnosis" and result.go_live_conditions:
        lines.append("Go-Live Conditions")
        lines.append("-" * 40)
        for condition in result.go_live_conditions:
            lines.append(f"  • {condition}")
        lines.append("")

    # G5: Summary
    if result.fusion_mode == "expert_diagnosis" and result.summary:
        lines.append("Summary")
        lines.append("-" * 40)
        lines.append(f"  {result.summary}")
        lines.append("")

    # Recommendation
    if result.recommendation:
        lines.append("Recommendation")
        lines.append("-" * 40)
        lines.append(result.recommendation.summary)
        lines.append("")
        lines.append(f"Decision: {result.recommendation.decision}")
        if result.recommendation.risks:
            lines.append("")
            lines.append("Risks:")
            for risk in result.recommendation.risks:
                lines.append(f"- {risk}")
        if result.recommendation.next_actions:
            lines.append("")
            lines.append("Next Actions:")
            for action in result.recommendation.next_actions:
                lines.append(f"- {action}")

    # Warnings
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"- {warning}")

    # Partial success indicator
    if result.partial_success:
        lines.append("")
        lines.append("[Partial Success] Some participants did not complete successfully.")

    return "\n".join(lines)


def fuse_command(
    group_id: str,
    question: str,
    participants: list[str],
    driver_bot_id: Optional[str] = None,
    timeout_ms: int = 15000,
    parallel: bool = True,
    strict_participants: bool = True,
    json_output: bool = False,
    request_id: Optional[str] = None,
    fusion_mode: Literal["agent", "conflict_alignment", "expert_diagnosis"] = "agent",
    service: Optional[GroupFusionService] = None,
    return_exit_code: bool = False,
) -> FusionResult | int:
    """
    执行 fuse 命令

    Args:
        group_id: Group ID
        question: 问题
        participants: 参与者列表
        driver_bot_id: Driver bot ID
        timeout_ms: 超时时间
        parallel: 是否并行
        strict_participants: 是否严格模式
        json_output: 是否 JSON 输出
        request_id: 请求 ID
        fusion_mode: 融合模式（agent / conflict_alignment / expert_diagnosis）
        service: 服务实例（用于测试注入）
        return_exit_code: 是否返回退出码而非结果

    Returns:
        FusionResult 或退出码
    """
    # 创建请求
    options = FuseOptions(
        timeout_ms=timeout_ms,
        parallel=parallel,
        strict_participants=strict_participants,
    )

    metadata = None
    if request_id:
        metadata = FuseMetadata(request_id=request_id, source="bcs-cli")

    request = FusionRequest(
        question=question,
        participants=participants,
        driver_bot_id=driver_bot_id,
        fusion_mode=fusion_mode,
        options=options,
        metadata=metadata,
    )

    # 获取服务
    if service is None:
        service = get_default_service()

    # 执行融合
    result = service.fuse(request, group_id=group_id)

    # 输出结果
    if json_output:
        output = json.dumps(result.model_dump(), indent=2, default=str, ensure_ascii=False)
    else:
        output = format_pretty_output(result)

    print(output)

    if return_exit_code:
        return ExitCode.SUCCESS

    return result


def main(args: Optional[Sequence[str]] = None) -> int:
    """
    CLI 主入口

    Args:
        args: 命令行参数

    Returns:
        退出码
    """
    try:
        parsed = parse_args(args)

        if parsed.command is None:
            parse_args(["--help"])
            return ExitCode.INVALID_ARGUMENT

        if parsed.command == "fuse":
            # 解析 participants
            participants = [p.strip() for p in parsed.participants.split(",")]

            # 处理 flag 冲突
            parallel = not parsed.no_parallel if hasattr(parsed, 'no_parallel') else parsed.parallel
            strict = not parsed.no_strict_participants if hasattr(parsed, 'no_strict_participants') else parsed.strict_participants

            return fuse_command(
                group_id=parsed.group,
                question=parsed.question,
                participants=participants,
                driver_bot_id=parsed.driver,
                timeout_ms=parsed.timeout,
                parallel=parallel,
                strict_participants=strict,
                json_output=parsed.json_output,
                request_id=parsed.request_id,
                fusion_mode=parsed.fusion_mode,
                service=None,  # 使用默认服务
                return_exit_code=True,
            )

        return ExitCode.SUCCESS

    except SystemExit as e:
        return e.code if isinstance(e.code, int) else ExitCode.UNKNOWN_ERROR

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return ExitCode.UNKNOWN_ERROR


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "main",
    "fuse_command",
    "parse_args",
    "ExitCode",
]