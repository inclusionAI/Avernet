"""Relay dispatch decision evidence, ticket, and Runner delivery operations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskGraphPatch,
    TaskNodePatch,
    task_spec_instruction,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)

logger = logging.getLogger("task.relay.search")


_SEARCH_EVIDENCE_FIELDS = {
    "query",
    "search_result",
    "candidate_evaluations",
    "origin_node_id",
}
_DECISIONS = {
    "selected_as_driver",
    "selected_as_worker",
    "rejected",
    "excluded_current_holder",
}


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskStateError(f"relay search evidence requires {name}")
    return value.strip()


def _require_score(value: Any, bot_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskStateError(f"relay candidate score must be numeric bot={bot_id}")
    score = float(value)
    if not 0.0 <= score <= 100.0:
        raise TaskStateError(f"relay candidate score must be in [0,100] bot={bot_id}")
    return score


def _identity(value: str) -> frozenset[str]:
    raw = value.strip()
    base = raw.partition(":")[0]
    return frozenset({raw, base} if base != raw else {raw})


def _base(value: str) -> str:
    return value.strip().partition(":")[0]


def _candidate_identities(candidates: list[dict[str, Any]]) -> list[frozenset[str]]:
    results: list[frozenset[str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        raw = str(candidate.get("bot_uuid") or candidate.get("bot_id") or "").strip()
        if not raw:
            raise TaskStateError("relay search candidate requires bot_uuid or bot_id")
        if raw in seen:
            continue
        seen.add(raw)
        results.append(_identity(raw))
    return results


@dataclass(frozen=True)
class RelaySearchEvidence:
    """A non-scheduling snapshot of the search evidence behind a decision."""

    query: str
    origin_node_id: str
    search_result: dict[str, Any]
    candidate_evaluations: list[dict[str, Any]]
    selected_bot_ids: tuple[str, ...]

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        origin_node_id: str,
        selected_bot_ids: list[str],
    ) -> "RelaySearchEvidence | None":
        raw = payload.get("search_evidence")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise TaskStateError("relay search_evidence must be an object")
        if set(raw) - _SEARCH_EVIDENCE_FIELDS:
            raise TaskStateError("relay search_evidence contains unsupported fields")

        query = _require_str(raw.get("query"), "search query")
        reported_origin = _require_str(raw.get("origin_node_id"), "origin_node_id")
        if reported_origin != origin_node_id:
            raise TaskStateError(
                "relay search evidence origin must match the current dispatch origin"
            )

        search_result = raw.get("search_result")
        raw_candidates = (
            search_result.get("candidates") if isinstance(search_result, dict) else None
        )
        if not isinstance(search_result, dict) or not isinstance(raw_candidates, list):
            raise TaskStateError(
                "relay search_evidence.search_result.candidates must be a list"
            )
        candidates = [item for item in raw_candidates if isinstance(item, dict)]
        candidate_identities = _candidate_identities(candidates)

        evaluations_raw = raw.get("candidate_evaluations")
        if not isinstance(evaluations_raw, list):
            raise TaskStateError(
                "relay search_evidence.candidate_evaluations must be a list"
            )

        evaluations: list[dict[str, Any]] = []
        eval_identities: list[frozenset[str]] = []
        for item in evaluations_raw:
            if not isinstance(item, dict):
                raise TaskStateError("relay candidate evaluation must be an object")
            bot_id = _require_str(item.get("bot_id") or item.get("bot_uuid"), "bot_id")
            identity = _identity(bot_id)
            if not any(identity & candidate for candidate in candidate_identities):
                raise TaskStateError(
                    f"relay candidate evaluation references unknown bot={bot_id}"
                )
            if identity in eval_identities:
                raise TaskStateError(
                    f"relay candidate evaluation is duplicated bot={bot_id}"
                )
            eval_identities.append(identity)
            decision = _require_str(item.get("decision"), "decision")
            if decision not in _DECISIONS:
                raise TaskStateError(f"unsupported relay candidate decision={decision}")
            _require_score(item.get("score"), bot_id)
            _require_str(item.get("score_reason"), f"score_reason bot={bot_id}")
            if not isinstance(item.get("selected"), bool):
                raise TaskStateError(
                    f"relay candidate evaluation requires selected boolean bot={bot_id}"
                )
            if not item.get("selected") and not item.get("reject_reason"):
                raise TaskStateError(
                    f"rejected relay candidate requires reject_reason bot={bot_id}"
                )
            if item.get("selected") and decision not in {
                "selected_as_driver",
                "selected_as_worker",
            }:
                raise TaskStateError(
                    f"selected relay candidate decision is invalid bot={bot_id}"
                )
            evaluations.append(
                {
                    "bot_id": bot_id,
                    "score": item["score"],
                    "score_reason": item["score_reason"],
                    "selected": item["selected"],
                    "decision": decision,
                    "reject_reason": item.get("reject_reason"),
                }
            )

        if len(evaluations) != len(candidate_identities):
            raise TaskStateError(
                "relay search_evidence.candidate_evaluations must cover every search candidate"
            )

        selected_bases = {
            _base(str(item)) for item in selected_bot_ids if str(item).strip()
        }
        reported_selected_bases = {
            _base(str(item["bot_id"])) for item in evaluations if item.get("selected")
        }
        if selected_bases != reported_selected_bases:
            raise TaskStateError(
                "relay search evidence selection does not match DISPATCH_RESULT selection"
            )

        return cls(
            query=query,
            origin_node_id=origin_node_id,
            search_result=dict(search_result),
            candidate_evaluations=evaluations,
            selected_bot_ids=tuple(str(item) for item in selected_bot_ids),
        )

    def to_ext_info(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "origin_node_id": self.origin_node_id,
            "search_result": self.search_result,
            "candidate_evaluations": list(self.candidate_evaluations),
            "selected_bot_ids": list(self.selected_bot_ids),
        }


class TaskServiceRelayDispatchMixin:
    async def dispatch_task(
        self,
        *,
        task_id: str,
        origin_node_id: str,
        target_node_id: str,
        holder_id: str,
        relay_turn: str,
        dispatch_id: str,
    ) -> dict[str, Any]:
        relay = self._relay()
        _, node = self._relay_node(task_id, target_node_id)
        dispatch_state = str(
            node.run_info.extend_props.get("relay_dispatch_state") or ""
        )
        if node.run_info.extend_props.get("relay_dispatch_id") == dispatch_id:
            if dispatch_state == "DELIVERED" or (
                dispatch_state == "DELIVERING"
                and node.status == Status.RUNNING
            ):
                return {"ok": True, "idempotent": True, "node_id": target_node_id}
        if node.run_info.extend_props.get("relay_planned_by") != origin_node_id:
            raise TaskStateError("relay dispatch target was not planned by origin node")
        if node.status != Status.PENDING:
            raise TaskStateError(
                f"relay dispatch target must be PENDING node={target_node_id}"
            )
        if node.run_info.run_mode not in {"single_bot", "coop_group"}:
            raise TaskStateError(
                "relay dispatch requires a persisted dispatch decision"
            )
        if (
            node.run_info.extend_props.get("relay_dispatch_id") == dispatch_id
            and dispatch_state == "DELIVERING"
        ):
            # The prior attempt may have consumed its ticket before the delivery
            # outcome was persisted. Restore the same ticket so retrying this
            # dispatch_id re-delivers rather than returning a false idempotent.
            try:
                self._report_relay_turn(
                    "RELAY_TURN_REOPEN",
                    task_id=task_id,
                    node_id=origin_node_id,
                    holder_id=holder_id,
                    token=relay_turn,
                )
            except TaskStateError:
                # The ticket can already be GRANTED (for example after an earlier
                # failed delivery). The normal require below still validates it.
                pass
        try:
            turn_node_id = relay.require(task_id, origin_node_id, holder_id, relay_turn)
        except TaskStateError as exc:
            self._emit_relay(
                task_id=task_id,
                node_id=target_node_id,
                action_result="turn_invalid",
                error_type=ReasonCatalog.RELAY,
                error_msg=str(exc),
                attempt=self._relay_attempt(task_id),
                ext_info={"holder_id": holder_id, "relay_turn_prefix": relay_turn[:8]},
            )
            raise
        if turn_node_id != origin_node_id:
            raise TaskStateError("relay dispatch origin does not own current turn")
        formation = None
        if node.run_info.run_mode == "coop_group":
            raw = node.run_info.extend_props.get("pending_group_formation") or {}
            formation = GroupFormation.from_dict(raw)
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=target_node_id,
                extend_props_patch={
                    "relay_dispatch_id": dispatch_id,
                    "relay_dispatch_state": "DELIVERING",
                },
            )
        )
        self._report_relay_turn(
            "RELAY_TURN_CONSUME",
            task_id=task_id,
            node_id=origin_node_id,
            holder_id=holder_id,
            token=relay_turn,
        )
        try:
            extend_patch: dict[str, Any] = {}
            if formation is not None:
                group_id = await self._relay_adapter.runner.form_coop_group(formation)
                extend_patch["group_id"] = group_id
            if extend_patch:
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=target_node_id,
                        extend_props_patch=extend_patch,
                    )
                )
            refreshed = self._relay_node(task_id, target_node_id)[1]
            delivered = bool(
                (await self._relay_adapter.runner.start_run([refreshed]))[0]
            )
        except Exception:
            logger.exception(
                "[task][relay] dispatch failed task=%s origin=%s target=%s",
                task_id,
                origin_node_id,
                target_node_id,
            )
            delivered = False
        if not delivered:
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=target_node_id,
                    failure_reason="下一棒 Runner 派发失败",
                    extend_props_patch={
                        "relay_dispatch_id": None,
                        "relay_dispatch_state": None,
                        "group_id": None,
                    },
                )
            )
            self._report_relay_turn(
                "RELAY_TURN_REOPEN",
                task_id=task_id,
                node_id=origin_node_id,
                holder_id=holder_id,
                token=relay_turn,
            )
            self._emit_relay(
                task_id=task_id,
                node_id=target_node_id,
                action_result="dispatch_failed_reopen",
                error_type=ReasonCatalog.RELAY,
                error_msg=f"relay dispatch failed node={target_node_id}",
                status_from=Status.PENDING,
                status_to=Status.PENDING,
                attempt=self._relay_attempt(task_id),
                ext_info={"holder_id": holder_id, "dispatch_id": dispatch_id},
            )
            raise TaskStateError(f"relay dispatch failed node={target_node_id}")
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=target_node_id,
                status=Status.RUNNING,
                progress_reason="下一棒 Runner 已完成实际投递",
                extend_props_patch={"relay_dispatch_state": "DELIVERED"},
            )
        )
        refreshed = self._relay_node(task_id, target_node_id)[1]
        self._emit_relay(
            task_id=task_id,
            node_id=target_node_id,
            action_result="dispatch",
            status_from=Status.PENDING,
            status_to=Status.RUNNING,
            attempt=self._relay_attempt(task_id),
            ext_info={
                "holder_id": holder_id,
                "dispatch_id": dispatch_id,
                "run_mode": refreshed.run_info.run_mode,
                "assignee": refreshed.run_info.assignee,
            },
        )
        return {
            "ok": True,
            "origin_node_id": origin_node_id,
            "target_node_id": target_node_id,
            "run_mode": refreshed.run_info.run_mode,
            "assignee": refreshed.run_info.assignee,
            "group_id": refreshed.run_info.extend_props.get("group_id"),
        }

    def _schedule_relay_bbs_selection(self, task_id: str, node_id: str) -> None:
        """Run the centralized BBS roster/bid selector on an existing Relay node."""
        node = self._relay_node(task_id, node_id)[1]
        task = asyncio.create_task(self._relay_adapter.runner.start_run([node]))
        tasks = getattr(self, "_bg_tasks", None)
        if isinstance(tasks, set):
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        logger.info(
            "[task][relay-bbs] scheduled centralized dynamic selection task=%s node=%s",
            task_id,
            node_id,
        )

    async def _apply_search_result(
        self,
        graph,
        node,
        holder_id,
        payload,
        relay_turn,
        progress_reason,
        failure_reason,
    ) -> dict[str, Any]:
        if node.status != Status.PENDING:
            raise TaskStateError(
                f"relay dispatch decision target must be PENDING node={node.node_id}"
            )
        outcome = str(payload.get("outcome") or "").upper()
        next_bots = [
            str(item).strip()
            for item in (payload.get("next_relay_bots") or payload.get("bot_ids") or [])
            if str(item).strip()
        ]
        driver = str(
            payload.get("driver_bot_id")
            or payload.get("assignee")
            or (next_bots[0] if next_bots else "")
        ).strip()
        selected_bot_ids = list(next_bots)
        if outcome == "HIT_SINGLE" and not selected_bot_ids and driver:
            selected_bot_ids = [driver]
        origin_node_id = str(
            node.run_info.extend_props.get("relay_planned_by") or ""
        ).strip()
        if not origin_node_id:
            raise TaskStateError(
                f"relay dispatch decision requires origin_node_id node={node.node_id}"
            )
        search_evidence = RelaySearchEvidence.from_payload(
            payload,
            origin_node_id=origin_node_id,
            selected_bot_ids=selected_bot_ids,
        )
        holder_base = str(holder_id).partition(":")[0]
        selected_bases = {
            item.partition(":")[0]
            for item in [driver, *next_bots]
            if item.partition(":")[0]
        }
        if holder_base and holder_base in selected_bases:
            raise TaskStateError(
                "relay dispatch cannot select the current holder as the next relay bot"
            )
        if outcome == "HIT_SINGLE":
            if not next_bots and driver:
                next_bots = [driver]
            if len(next_bots) != 1 or driver != next_bots[0]:
                raise TaskStateError(
                    "HIT_SINGLE requires one next_relay_bot equal to driver_bot_id"
                )
            patch = TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                run_mode="single_bot",
                assignee=driver,
                progress_reason=progress_reason,
                failure_reason=failure_reason,
                extend_props_patch={
                    "relay_holder_id": driver,
                    "driver_bot_id": driver,
                    "next_relay_bots": next_bots,
                },
            )
        elif outcome == "HIT_MULTI_BOTS":
            if len(next_bots) < 2 or not driver or driver not in next_bots:
                raise TaskStateError(
                    "HIT_MULTI_BOTS requires driver_bot_id in next_relay_bots"
                )
            members_info = payload.get("members_info") or [
                {
                    "bot_id": bot_id,
                    "role": "manager" if bot_id == driver else "worker",
                }
                for bot_id in next_bots
            ]
            formation = GroupFormation(
                bot_ids=next_bots,
                collab_mode=str(payload.get("collab_mode") or "manager_worker"),
                group_name=payload.get("group_name"),
                members_info=members_info,
                extend_props={
                    **dict(payload.get("group_extend_props") or {}),
                    "relay_execution": True,
                    "dynamic_task_node_protocol": True,
                    "task_id": graph.task_id,
                    "loop_task_id": f"{graph.task_id}::{node.node_id}",
                    "task_objective": node.task_spec.goal.objective,
                    "task_instruction": task_spec_instruction(node.task_spec),
                    "task_context": node.task_spec.context.background,
                    "acceptances": [
                        item.to_dict() for item in node.task_spec.goal.acceptances
                    ],
                    "manager_bot_id": driver,
                    "originator_bot_id": driver,
                    "owner_user_id": graph.extend_props.get("owner_user_id"),
                },
            )
            patch = TaskNodePatch(
                task_id=graph.task_id,
                node_id=node.node_id,
                run_mode="coop_group",
                assignee=driver,
                progress_reason=progress_reason,
                failure_reason=failure_reason,
                extend_props_patch={
                    "pending_group_formation": formation.to_dict(),
                    "relay_holder_id": driver,
                    "driver_bot_id": driver,
                    "next_relay_bots": next_bots,
                },
            )
        elif outcome == "MISS":
            reason = failure_reason or str(
                payload.get("miss_reason") or "搜推没有匹配结果"
            )
            self._report_node_patch(
                TaskNodePatch(
                    task_id=graph.task_id,
                    node_id=node.node_id,
                    run_mode="bbs",
                    progress_reason=progress_reason,
                    failure_reason=reason,
                    extend_props_patch={
                        "driver_bot_id": None,
                        "next_relay_bots": [],
                    },
                )
            )
            self._report_graph_patch(
                graph.task_id,
                TaskGraphPatch(
                    extend_props_patch={"bbs_mode": True, "bbs_node_id": node.node_id}
                ),
            )
            self._report_relay_turn(
                "RELAY_TURN_CONSUME",
                task_id=graph.task_id,
                node_id=node.node_id,
                holder_id=holder_id,
                token=relay_turn,
            )
            self._schedule_relay_bbs_selection(graph.task_id, node.node_id)
            self._emit_relay(
                task_id=graph.task_id,
                node_id=node.node_id,
                action_result="miss",
                attempt=self._relay_attempt(graph.task_id),
                boost_reason=progress_reason,
                ext_info={
                    "published_bbs": True,
                    "miss_reason": reason,
                    "holder_id": holder_id,
                    "origin_node_id": origin_node_id,
                    **(
                        {"search_evidence": search_evidence.to_ext_info()}
                        if search_evidence
                        else {}
                    ),
                },
            )
            return {"ok": True, "published_bbs": True, "node_id": node.node_id}
        else:
            raise TaskStateError(f"unsupported dispatch outcome={outcome}")
        self._report_node_patch(patch)
        self._emit_relay(
            task_id=graph.task_id,
            node_id=node.node_id,
            action_result="hit_single" if outcome == "HIT_SINGLE" else "hit_multi",
            attempt=self._relay_attempt(graph.task_id),
            boost_reason=progress_reason,
            ext_info={
                "driver_bot_id": driver,
                "next_relay_bots": next_bots,
                "holder_id": holder_id,
                "origin_node_id": origin_node_id,
                "planned_by": node.run_info.extend_props.get("relay_planned_by"),
                **(
                    {"search_evidence": search_evidence.to_ext_info()}
                    if search_evidence
                    else {}
                ),
            },
        )
        return {
            "ok": True,
            "published_bbs": False,
            "node_id": node.node_id,
            "driver_bot_id": driver,
            "next_relay_bots": next_bots,
        }
