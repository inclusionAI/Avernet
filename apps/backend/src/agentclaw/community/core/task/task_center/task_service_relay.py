"""TaskService's single distributed Relay report and candidate-search contract."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.search_viewer import SearchViewerType, normalize_search_viewer
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    BotCandidate,
    NodeAction,
    Status,
    TaskCallbackData,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportEventType,
    TaskReportResult,
    TaskRuntimeProfile,
    task_spec_instruction,
)
from agentclaw.community.core.task.task_runner.client.relay_prompt import (
    format_relay_execution_instruction,
)
from agentclaw.community.utils.env_utils import get_current_env

logger = logging.getLogger("task.relay")

# Temporary duty-domain fallback. Search quality work is still in progress;
# once the normal catalog consistently returns duty bots, remove this table
# pair and the private fallback helper below with it. Bot catalogs are
# environment-isolated, so each environment only receives its own identities.
_TEMPORARY_DUTY_CANDIDATES: tuple[BotCandidate, ...] = (
    BotCandidate(
        bot_id="20260407_025zmn4f:378611",
        name="RiskProfilePlus BOT",
        description="RP+ platform diagnostics",
    ),
    BotCandidate(
        bot_id="20260403_wb2jcaof:328947",
        name="UCT2.0 值班助手",
        description="Strategy and dispatch diagnostics",
    ),
    BotCandidate(
        bot_id="20260525_civbnttp:453718",
        name="aliuct 值班助手",
        description="UCT/aliuct strategy diagnostics",
    ),
    BotCandidate(
        bot_id="20260407_jmg31orw:152819",
        name="FeaturePro",
        description="Feature creation and verification diagnostics",
    ),
    BotCandidate(
        bot_id="20260415_w7wokfyw:323749",
        name="处罚中心官方答疑助手",
        description="Penalty and appeal guidance",
    ),
    BotCandidate(
        bot_id="20260724_tilcdwii:272477",
        name="yofa-agent",
        description="Natural-language data and feature generation",
    ),
)
_TEMPORARY_PRE_DUTY_CANDIDATES: tuple[BotCandidate, ...] = (
    BotCandidate(
        bot_id="20261020_xa4pi64f:146836",
        name="RiskProfilePlus-任务分身",
        description="RP+平台：securitydataservice 取数、ODPS 回流、View 构建、traceId 诊断",
    ),
    BotCandidate(
        bot_id="20261014_dpv2dfuk:35983",
        name="UCT20值班助手",
        description="策略配置 / 切流评估 / Trace 耗时 / 策略下发链路 / 下发链路监控",
    ),
    BotCandidate(
        bot_id="20261014_im6mcv40:35983",
        name="aliuct值班助手",
        description="UCT1.0 / aliuct：策略拦截 / 超时 / 未命中 / 引擎答疑 / 策略仿真",
    ),
    BotCandidate(
        bot_id="20261014_fnr8p3xe:35983",
        name="FeaturePro",
        description="FeaturePro 平台：特征创建/迁移/验证/报错诊断",
    ),
)


class TaskServiceRelayMixin:
    _temporary_duty_candidates: tuple[BotCandidate, ...] = _TEMPORARY_DUTY_CANDIDATES
    _temporary_pre_duty_candidates: tuple[BotCandidate, ...] = _TEMPORARY_PRE_DUTY_CANDIDATES

    def _build_relay_root_execution_instruction(self, task_id: str) -> str:
        """Build the first baton for the existing requester/Bot session."""
        backend_url = str(self._api_base_url or "").strip().rstrip("/")
        if not backend_url:
            raise TaskStateError(
                "relay task creation requires task_dispatch.backend_url"
            )
        graph = self._graph.query_task_dashboard(task_id)
        root = next(
            (node for node in graph.tasks if node.node_id == task_id), None
        )
        if root is None:
            raise TaskStateError("relay root node is unavailable")
        relay_token = str(root.run_info.extend_props.get("relay_token") or "")
        if not relay_token:
            raise TaskStateError("relay root node has no run generation")
        execution_config = graph.extend_props.get("execution_config") or {}
        return format_relay_execution_instruction(
            task_id=task_id,
            node_id=root.node_id,
            relay_token=relay_token,
            backend_url=backend_url,
            objective=root.task_spec.goal.objective,
            instruction=task_spec_instruction(root.task_spec),
            acceptances=[
                {"id": item.id, "description": item.description}
                for item in root.task_spec.goal.acceptances
            ],
            allowed_run_modes=list(TaskRuntimeProfile.run_modes_from_graph(graph)),
            source_channel=execution_config.get("source_channel"),
        )

    async def report(self, command: TaskReportCommand) -> TaskReportResult:
        """Apply the business report independently of optional observation data.

        A supplied trace is still emitted step by step, best-effort, without
        duplicating replayed events. Reports without trace neither emit rows nor
        perform the observation-only replay precheck. Business action logs and
        persisted search receipts remain authoritative regardless of trace.
        """
        self._load_relay_report_action_log(command)
        has_trace = command.trace is not None and bool(command.trace.steps)
        already_known = has_trace and self._relay_report_event_known(command)
        result = self._graph.apply_relay_report(command)
        if has_trace and not already_known:
            self._emit_trace_step_rows(command)
        return result

    def _load_relay_report_action_log(self, command: TaskReportCommand) -> None:
        """Hydrate persisted search receipts on the report worker.

        ``load_graph()`` intentionally does not hydrate diagnostic action logs.
        A successor PLAN_RESULT, however, validates the assignee against the
        current generation's persisted search receipt.  /search and report can
        land on different application workers, so the report worker must load
        the node action history after hydrating the graph and before applying
        the successor plan.
        """
        if (
            command.event_type != TaskReportEventType.PLAN_RESULT
            or command.event is None
        ):
            return
        graph = self._graph.query_task_dashboard(command.task_id)
        self._graph.load_action_logs(graph)

    def _relay_report_event_known(self, command: TaskReportCommand) -> bool:
        """该 ``event_id`` 是否已有 relay 回执(= 重放上报)。读图失败 → False
        (best-effort:预查不可用时宁可重发,不丢观测)。"""
        try:
            graph = self._graph.query_task_dashboard(command.task_id)
            known = set(
                (graph.extend_props.get("_relay_report_event_ids") or {}).keys()
            )
            return command.event_id in known
        except Exception:  # noqa: BLE001  观测旁路门闸,吞而不抛
            logger.warning(
                "[task][relay][trajectory] 上报预查失败,按未见过处理 "
                "task=%s event=%s",
                command.task_id,
                command.event_id,
                exc_info=True,
            )
            return False

    def _emit_trace_step_rows(self, command: TaskReportCommand) -> None:
        """trace.steps 逐条写时落表:一 step 一行,七字段与轨迹表对齐。

        ``now_ms`` 以当前时刻为基准按 step 序 +1 偏移,保证同一上报的 steps 在
        timeline 内先后有序;``action_type`` 即 Bot 侧 step 码(如 S1_CONTEXT,
        2026-10-15 起为轨迹**主事件**的 action_type,直接透传不再限定枚举)。
        ``ext_info`` 带 ``trace_step`` + ``relay_event_id`` 可追溯。"""
        if self._task_context_service is None:
            return
        base_ts = int(time.time() * 1000)
        for offset, step in enumerate(command.trace.steps, start=1):
            self._task_context_service.emit_trajectory_event(
                command.task_id,
                command.node_id,
                step.action_type,
                action_result=step.action_result,
                action_input=step.action_input,
                status_from=step.status_from,
                status_to=step.status_to,
                attempt=0,
                boost_reason=step.boost_reason,
                error_msg=step.error_msg,
                ext_info={
                    "trace_step": True,
                    "relay_event_id": command.event_id,
                },
                now_ms=base_ts + offset,
            )

    def _report_fact(self, report_type: str, payload: dict[str, Any]) -> Any:
        """Preserve the GraphService fact seam used by centralized BBS too."""
        return self._graph.report(TaskCallbackData(data={
            "report_type": report_type,
            "payload": payload,
        }))

    def _report_node_patch(self, patch: TaskNodePatch) -> Any:
        return self._report_fact("NODE_PATCH", {"patch": patch})

    def _temporary_duty_fallback(
        self, candidates: list[BotCandidate]
    ) -> tuple[list[BotCandidate], str, int]:
        """Apply the temporary duty fallback directly inside search.

        This is intentionally one small, removable policy method in the search
        interface rather than a separate pluggable module.  Prod and pre use the
        same semantic-search result first and append only their own environment
        duty fallback.  If normal search already returns one duty bot, keep the
        search result unchanged so the fallback does not overwhelm relevance
        ranking.
        """
        environment = get_current_env()
        if environment == "prod":
            duty_candidates = self._temporary_duty_candidates
        elif environment == "pre":
            duty_candidates = self._temporary_pre_duty_candidates
        else:
            return candidates, "search", 0

        duty_ids = {candidate.bot_id for candidate in duty_candidates}
        if any(candidate.bot_id in duty_ids for candidate in candidates):
            return candidates, "search", 0
        return (
            [*candidates, *duty_candidates],
            "search_plus_fallback",
            len(duty_candidates),
        )

    async def search_bots(
        self, *, query: str, task_id: str, node_id: str, relay_token: str,
        viewer_actor_type: SearchViewerType | None = None,
        viewer_actor_id: str | None = None,
        visibility: str | None = None,
    ) -> list[BotCandidate]:
        """Project the catalog result to the stable Relay BotCandidate model."""
        if not query.strip() or not task_id or not node_id:
            raise TaskStateError("relay search requires query, task_id and node_id")
        viewer = normalize_search_viewer(viewer_actor_type, viewer_actor_id)
        relay_token_fingerprint = hashlib.sha256(relay_token.encode()).hexdigest()
        try:
            raw = await self._relay_adapter.search.search_catalog(
                query, visibility=visibility, **viewer
            )
        except Exception as exc:
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "search",
                    "action_result": "failed",
                    "query": query,
                    "error": f"{type(exc).__name__}: {exc}",
                    "relay_token_fingerprint": relay_token_fingerprint,
                },
            )
            raise
        candidates: list[BotCandidate] = []
        for item in raw.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            bot_id = str(item.get("bot_uuid") or item.get("bot_id") or "").strip()
            if not bot_id:
                continue
            score_raw = (item.get("recommend") or {}).get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            candidates.append(BotCandidate(
                bot_id=bot_id,
                name=str(item.get("bot_name") or item.get("name") or bot_id),
                description=str(item.get("bot_desc") or item.get("description") or ""),
                score=score,
            ))
        candidates, candidate_source, fallback_candidate_count = (
            self._temporary_duty_fallback(candidates)
        )
        self._graph.append_action_event(
            task_id,
            node_id,
            NodeAction.DISPATCH,
            {
                "phase": "search",
                "action_result": "search",
                "query": query,
                "relay_token_fingerprint": relay_token_fingerprint,
                "candidate_count": len(candidates),
                "candidates": [
                    {
                        "bot_id": candidate.bot_id,
                        "name": candidate.name,
                        "score": candidate.score,
                    }
                    for candidate in candidates
                ],
                "candidate_source": candidate_source,
                "fallback_candidate_count": fallback_candidate_count,
                "raw_item_count": raw.get("raw_item_count"),
                "failed_keywords": list(raw.get("failed_keywords") or []),
            },
        )
        return candidates

    def _claim_relay_bbs(
        self, task_id: str, node_id: str, bot_id: str,
        claim_id: str | None = None,
    ):
        graph = self._graph.query_task_dashboard(task_id)
        if not graph.is_relay:
            raise TaskStateError("BBS node is not in relay mode")
        node = next((item for item in graph.tasks if item.node_id == node_id), None)
        if node is None or node.run_info.run_mode != "bbs" or node.status != Status.PENDING:
            raise TaskStateError("relay BBS node is not claimable")
        return self._graph.claim_bbs_owner(
            task_id, bot_id, node_id=node_id, claim_id=claim_id,
        )
