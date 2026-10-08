"""Independent TopN task execution and owner-Bot result selection."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import Any

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskCallbackData,
    TaskNode,
    TaskNodeQueryCriteria,
)
from agentclaw.community.core.task.task_dispatch.rationale import (
    _extract_skill_response_content,
)
from agentclaw.community.core.task.task_runner.client.translators import (
    SingleBotRunTranslator,
)

logger = logging.getLogger("task.multi_sample")


class MultiSampleExecutor:
    """Run ranked Bot candidates independently and report one canonical result.

    The logical graph still contains one ``TaskNode``. Sample executions never
    push node callbacks themselves; this coordinator emits exactly one callback
    after the owner Bot judges all successful outputs. That keeps graph writes
    and lifecycle progression on the existing ``TaskGraphService.report`` path.
    """

    def __init__(self, *, bot, graph, context, formatter, sink) -> None:
        self._bot = bot
        self._graph = graph
        self._context = context
        self._formatter = formatter
        self._sink = sink
        self._tasks: set[asyncio.Task] = set()

    def can_handle(self, node: TaskNode) -> bool:
        samples = node.run_info.extend_props.get("dispatch_samples")
        return (
            node.run_info.run_mode == "single_bot"
            and isinstance(samples, list)
            and len(samples) > 1
        )

    def start(self, node: TaskNode) -> bool:
        if not self.can_handle(node):
            return False
        task = asyncio.create_task(self._run(node))
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return True

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except Exception:  # noqa: BLE001 background failure must be observable
            logger.exception("[task][multi-sample] background execution crashed")

    async def _run(self, node: TaskNode) -> None:
        # ``TaskRunner._drain`` flips PENDING -> RUNNING immediately after
        # ``TaskExecutor.dispatch`` returns. Do not let a very fast test/fake Bot
        # report a terminal result before that authoritative transition lands.
        await self._wait_until_running(node)
        samples = list(node.run_info.extend_props.get("dispatch_samples") or [])
        results = await asyncio.gather(
            *[self._execute_sample(node, sample) for sample in samples]
        )
        completed = [result for result in results if result["completed"]]
        if not completed:
            await self._report_error(node, "multi_sample_all_failed", results)
            return

        selected, judge_meta = await self._select_best(node, completed)
        canonical_result = {
            "success": selected["success"],
            "data": selected["output"],
            "gaps": selected["gaps"],
            "_ext_info": {
                "multi_sample": {
                    "requested": len(samples),
                    "completed": len(completed),
                    "selected_sample_id": selected["sample_id"],
                    "selected_bot_id": selected["bot_id"],
                    "judge": judge_meta,
                    "samples": [self._result_summary(item) for item in results],
                }
            },
        }
        await self._sink.report_result(
            TaskCallbackData(
                data={
                    "loop_task_id": f"{node.task_id}::{node.node_id}",
                    "workflow_type": "multi_sample",
                    "result": canonical_result,
                }
            )
        )

    async def _wait_until_running(self, node: TaskNode) -> None:
        if self._graph is None:
            return
        for _ in range(200):
            try:
                hits = self._graph.query_task_nodes(
                    node.task_id,
                    TaskNodeQueryCriteria(node_ids=[node.node_id]),
                )
                if hits and hits[0].status == Status.RUNNING:
                    return
            except Exception:  # noqa: BLE001 lightweight test graph may omit query
                return
            await asyncio.sleep(0.01)

    async def _execute_sample(
        self, node: TaskNode, sample: dict[str, Any]
    ) -> dict[str, Any]:
        sample_id = str(sample.get("sample_id") or "")
        bot_id = str(sample.get("bot_id") or "")
        try:
            sample_node = dataclasses.replace(
                node,
                run_info=dataclasses.replace(
                    node.run_info,
                    assignee=bot_id,
                    extend_props=dict(node.run_info.extend_props),
                ),
            )
            context = dict(self._context.build(node.task_id, node.node_id) or {})
            context.update(
                {
                    "task_id": node.task_id,
                    "node_id": node.node_id,
                    "execution_mode": "single_bot",
                    "skill_report_enabled": False,
                }
            )
            message = self._formatter.format_execute(context, sample_node)
            run = await self._bot.send_and_wait_async(
                bot_id=bot_id,
                message=message,
                metadata={
                    "phase": "task_multi_sample",
                    "biz_task_id": node.task_id,
                    "sample_id": sample_id,
                },
            )
            callback = SingleBotRunTranslator.adapt(
                run, f"{node.task_id}::{node.node_id}"
            )
            terminal = callback.data.get("result", {})
            error = terminal.get("exec_error")
            completed = not error and type(terminal.get("success")) is bool
            return {
                "sample_id": sample_id,
                "bot_id": bot_id,
                "rank": sample.get("rank"),
                "completed": completed,
                "success": bool(terminal.get("success")) if completed else False,
                "output": terminal.get("data"),
                "gaps": list(terminal.get("gaps") or []),
                "error": None if completed else str(error or "terminal_result_invalid"),
            }
        except Exception as exc:  # noqa: BLE001 one failed sample must not cancel peers
            logger.warning(
                "[task][multi-sample] task=%s node=%s sample=%s bot=%s failed: %s",
                node.task_id,
                node.node_id,
                sample_id,
                bot_id,
                exc,
            )
            return {
                "sample_id": sample_id,
                "bot_id": bot_id,
                "rank": sample.get("rank"),
                "completed": False,
                "success": False,
                "output": None,
                "gaps": [],
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }

    async def _select_best(
        self, node: TaskNode, successful: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        accepted = [item for item in successful if item["success"]]
        fallback_pool = accepted or successful
        fallback = min(fallback_pool, key=lambda item: int(item.get("rank") or 10**9))
        owner = self._owner_bot_id(node)
        if not owner:
            return fallback, {"mode": "rank_fallback", "reason": "owner_missing"}
        prompt = self._judge_prompt(node, successful)
        try:
            run = await self._bot.send_and_wait_async(
                bot_id=owner,
                message=prompt,
                metadata={
                    "phase": "task_multi_sample_judge",
                    "biz_task_id": node.task_id,
                },
            )
            if str(run.get("status") or "").lower() != "completed":
                raise ValueError("judge run did not complete")
            content = _extract_skill_response_content(run)
            payload = extract_json(content)
            selected_id = str(payload.get("selected_sample_id") or "")
            selected = next(
                (item for item in successful if item["sample_id"] == selected_id),
                None,
            )
            if selected is None:
                raise ValueError("judge selected an unknown sample")
            return selected, {
                "mode": "llm",
                "reason": str(payload.get("reason") or "")[:1000],
            }
        except Exception as exc:  # noqa: BLE001 deterministic ranked fallback
            logger.warning(
                "[task][multi-sample] task=%s node=%s judge failed, use rank fallback: %s",
                node.task_id,
                node.node_id,
                exc,
            )
            return fallback, {
                "mode": "rank_fallback",
                "reason": f"{type(exc).__name__}: {exc}"[:500],
            }

    def _owner_bot_id(self, node: TaskNode) -> str:
        graph = node.node_run_graph
        props = getattr(graph, "extend_props", {}) or {}
        if not props and self._graph is not None:
            try:
                snapshot = self._graph.query_task_dashboard(node.task_id)
                props = getattr(snapshot, "extend_props", {}) or {}
            except Exception:  # noqa: BLE001 unavailable graph uses ranked fallback
                props = {}
        return compose_bot_identity(
            str(props.get("owner_bot_id") or ""), props.get("owner_user_id")
        )

    @staticmethod
    def _judge_prompt(node: TaskNode, results: list[dict[str, Any]]) -> str:
        candidates = [
            {
                "sample_id": item["sample_id"],
                "bot_id": item["bot_id"],
                "rank": item["rank"],
                "success": item["success"],
                "output": item["output"],
                "gaps": item["gaps"],
            }
            for item in results
        ]
        acceptances = [
            {"id": item.id, "description": item.description}
            for item in node.task_spec.goal.acceptances
        ]
        return "\n".join(
            [
                "[task-multi-sample-judge]",
                "请根据任务目标和验收标准，从候选执行结果中选择效果最好的一个；优先选择验收通过且质量最高的结果。",
                "只返回 JSON，不要补充其它文本：",
                '{"selected_sample_id":"sample-N","reason":"选择理由"}',
                f"任务目标: {node.task_spec.goal.objective}",
                f"验收标准: {json.dumps(acceptances, ensure_ascii=False)}",
                f"候选结果: {json.dumps(candidates, ensure_ascii=False, default=str)}",
            ]
        )

    async def _report_error(
        self, node: TaskNode, error: str, results: list[dict[str, Any]]
    ) -> None:
        await self._sink.report_result(
            TaskCallbackData(
                data={
                    "loop_task_id": f"{node.task_id}::{node.node_id}",
                    "workflow_type": "multi_sample",
                    "result": {
                        "success": False,
                        "exec_error": error,
                        "_ext_info": {
                            "multi_sample": {
                                "samples": [
                                    self._result_summary(item) for item in results
                                ]
                            }
                        },
                    },
                }
            )
        )

    @staticmethod
    def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "sample_id": result.get("sample_id"),
            "bot_id": result.get("bot_id"),
            "rank": result.get("rank"),
            "completed": bool(result.get("completed")),
            "success": bool(result.get("success")),
            "gaps": list(result.get("gaps") or []),
            "error": result.get("error"),
        }
