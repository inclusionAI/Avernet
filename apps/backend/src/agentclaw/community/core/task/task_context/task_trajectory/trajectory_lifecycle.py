"""Trajectory emission and centralized lifecycle observation helpers."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.task_runner.centralized_support import (
    NodeAction,
    NodeOpResult,
    PlanResult,
    Status,
    TaskNodePatch,
    _EXEC_ERROR_MSG_MAX,
    _EXEC_ERROR_ORIGIN_TO_REASON,
    logger,
)
from agentclaw.community.core.task.task_context.task_trajectory.payloads import (
    emit_trajectory_event as _emit_trajectory_event,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TrajectoryActionType,
)


class TaskTrajectoryLifecycle:
    # Emit (write) — fire-and-forget event emission (decision #14). The
    # facade ``TaskContextService`` relays here; external callers never touch
    # the raw repo. Wraps payloads.emit_* with self._repo; the free funcs keep
    # the no-op-if-None + swallow+warn semantics unchanged.
    # ------------------------------------------------------------------

    def emit_trajectory_event(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType | str",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | str | None" = None,
        error_msg: str | None = None,
        ext_info: "dict[str, Any] | None" = None,
        status_from: "Status | str | None" = None,
        status_to: "Status | str | None" = None,
        attempt: int = 0,
        boost_reason: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Emit one trajectory event via the repo. Fire-and-forget (decision #14):
        never raises; ``self._repo is None`` (lightweight DI) → no-op."""
        _emit_trajectory_event(
            self._repo,
            task_id,
            node_id,
            action_type,
            action_result=action_result,
            action_input=action_input,
            error_type=error_type,
            error_msg=error_msg,
            ext_info=ext_info,
            status_from=status_from,
            status_to=status_to,
            attempt=attempt,
            boost_reason=boost_reason,
            now_ms=now_ms,
        )

    def _emit_plan_trajectory(
        self,
        task_id: str,
        target_id: str | None,
        pr: PlanResult,
        attempt: int,
        failure_msg: str | None,
    ) -> None:
        """REQ-3 (P3-2) 每次尝试(retry attempt)发射一条 PLAN 轨迹事件。

        additive 旁路:与既有 post-loop ``_log_action(NodeAction.PLAN, ...)`` 在同一
        闸门位置、互不相干,经 ``emit_trajectory_event`` 独立 direct-INSERT 到
        ``task_trajectory_events``(不走 ``task_action_log``)。``action_input=pr.prompt_digest``
        (workflow 策略 → None);失败 mid-row(``gap_detail`` 以 ``plan_`` 开头)带
        ``error_type=PLAN_FAILURE`` + 截断 ``error_msg`` + ``ext_info={strategy_name,
        gap_detail, raw_response_digest}``;成功条带 ``ext_info={strategy_name, has_gap,
        gap_detail, children, raw_response_digest}``、``error_*=None``。

        全程防御:emitter(``emit_trajectory_event``)兜一层 try/except + WARNING
        (决策 #14);ext_info 装配在当前 PlanResult 形状下不会抛,无独立内层 guard
        (避免静默 swallow 掉未来的装配 bug —— 决策 #14 要求失败可见:WARNING)。
        ``repo is None`` 时 emitter 静默 no-op(测试/轻量 DI 取不到协议)。"""
        if target_id is None:
            # 无锚定节点(无根/缺图)——不发射,跳过;正常路径下不应发生(根已就绪)。
            return
        try:
            gd = pr.gap_detail or ""
            is_failure = gd.startswith("plan_")
            if is_failure:
                # call_fail  = bot 调用没拿到可用的 COMPLETED 响应(传输/未完成:
                #   plan_call_fail=planner 抛异常;plan_not_completed=run 非 COMPLETED)
                # parse_fail = COMPLETED 响应但形态无法用(空 content / 解析失败 /
                #   形态非预期:plan_parse_fail / plan_shape_unexpected / plan_empty_content)
                # 对齐 TrajectoryEvent.action_result 词表;analyzer 据此归因 plan_failure 子类。
                action_result = "parse_fail" if gd in (
                    "plan_parse_fail", "plan_shape_unexpected", "plan_empty_content",
                ) else "call_fail"
                raw_msg = failure_msg or gd
                error_msg = raw_msg if len(raw_msg) <= 500 else raw_msg[:497] + "..."
            else:
                action_result = "success"
                error_msg = None
            # ext_info 装配:dict 字面量在当前 PlanResult 形状下不会抛;不做静默 swallow
            # (决策 #14 要求失败可见:WARNING)。未来的装配 bug 由外层 try/except +
            # logger.warning 统一捕获可见(此处无独立 guard)。
            if is_failure:
                ext_info: dict[str, Any] = {
                    "strategy_name": pr.strategy_name,
                    "gap_detail": gd or None,
                    "raw_response_digest": pr.raw_response_digest,
                }
            else:
                ext_info = {
                    "strategy_name": pr.strategy_name,
                    "has_gap": pr.has_gap,
                    "gap_detail": gd or None,
                    "children": list(pr.planned_children or []),
                    "raw_response_digest": pr.raw_response_digest,
                }
            self._log_trajectory(
                task_id,
                target_id,
                "plan",  # TrajectoryActionType.PLAN.value(TYPE_CHECKING-only enum;emitter 接受 str)
                action_result=action_result,
                action_input=pr.prompt_digest,
                error_type="plan_failure" if is_failure else None,
                error_msg=error_msg,
                ext_info=ext_info,
                status_from=Status.PLANNING,
                status_to=Status.PLANNING,
                attempt=attempt,
            )
        except Exception as ex:  # noqa: BLE001  轨迹旁路:吞而不抛 + WARNING(决策 #14)
            logger.warning(
                "[task][trajectory][plan] task=%s attempt=%d 发射失败:%s",
                task_id, attempt, ex,
            )

    def _log_action(
        self,
        task_id: str,
        node_id: str,
        action: NodeAction,
        payload: dict,
        *,
        attempt: int | None = None,
        status_from: Status | None = None,
        status_to: Status | None = None,
    ) -> None:
        """追加节点动作历史快照(append-only;零侵入驱动逻辑)。

        供各逻辑动作(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION)完成时调用,
        纯可观测旁路:不翻态、不读回驱动。``attempt`` 省略时取节点 harness_retries 快照;
        ``status_from``/``status_to`` 省略时由调用方按动作前/后态传(未翻态可不传)。
        """
        if attempt is None:
            node = next(
                (
                    n
                    for n in self._graph.query_task_dashboard(task_id).tasks
                    if n.node_id == node_id
                ),
                None,
            )
            attempt = (
                int(node.run_info.extend_props.get("harness_retries", 0)) if node else 0
            )
        try:
            self._graph.append_action_event(
                task_id,
                node_id,
                action,
                payload,
                attempt=attempt,
                status_from=status_from,
                status_to=status_to,
            )
        except Exception as ex:  # noqa: BLE001  历史快照写入失败不影响驱动
            logger.warning(
                "[task][action-log] task=%s node=%s action=%s 追加失败:%s",
                task_id,
                node_id,
                action.value,
                ex,
            )

    def _log_trajectory(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | None" = None,
        error_msg: str | None = None,
        ext_info: dict[str, Any] | None = None,
        status_from: Status | None = None,
        status_to: Status | None = None,
        attempt: int = 0,
    ) -> None:
        """旁路发射一条任务轨迹事件(REQ-11 采集层)。零侵入驱动逻辑:

        - 与 ``_log_action`` 在同一闸门位置调用,但**完全独立**——经
          ``self._task_context_service.emit_trajectory_event(...)`` 中转到内部
          ``TaskTrajectoryService`` 直接 INSERT 到 ``task_trajectory_events``,
          **不**经 ``self._graph.append_action_event``
          (plan §"Spec clarifications" #2:The trajectory path is independent and
          direct-INSERT;mirrors only the swallow + no re-raise pattern)。
        - ``self._task_context_service`` 为 ``None`` 时(测试/轻量 DI 取不到协议)→
          跳过发射静默 no-op,正向驱动不受影响(内部 repo-None 情形由 emitter 再兜底 no-op)。
        - 全程 ``try/except Exception`` 吞异常(**不**抛出),失败记 WARNING 日志
          (已确认决策 #14;AGENTS.md "propagate persistence write failures" 对此
          fire-and-forget 观测旁路**明示豁免**)——见 ``task_trajectory/payloads.py``。

        线程/调用约定:同步调用,可在各 gate 的锁内/锁外任意位置直接调用。
        ``action_input`` **不截断**(原文落库);``error_msg`` 由调用方(各 gate)截断后传入。
        ``now_ms`` 由 emitter 取当前 wall-clock(emitter 内有兜底),故本方法不暴露该参数。
        """
        # ``task_context_service is None`` (lightweight DI: trajectory unbound) → no-op;
        # when present, the service relays to the internal TaskTrajectoryService whose
        # emitter no-ops + swallows on repo-None (decision #14). The guard keeps the
        # gate concise regardless.
        if self._task_context_service is not None:
            self._task_context_service.emit_trajectory_event(
                task_id,
                node_id,
                action_type,
                action_result=action_result,
                action_input=action_input,
                error_type=error_type,
                error_msg=error_msg,
                ext_info=ext_info,
                status_from=status_from,
                status_to=status_to,
                attempt=attempt,
            )

    @staticmethod
    def _transition_action_result(status_to: Status | None) -> str:
        """Map a TRANSITION's ``status_to`` to a trajectory ``action_result``
        (REQ-1's open-ended ``...`` enumeration). The status_to-derived
        lowercase name mirrors the ``_log_action`` payload's ``to`` field and
        is what the analyzer's ``_terminal_status`` keys on (via ``status_to``
        membership in the terminal set, not ``action_result``). Mapping:

            SUCCESS→"success"   HUNG→"hung"   FAILED→"failed"
            CANCELLED→"cancelled"   DONE→"done"   RUNNING→"running"
            PENDING→"pending"   PLANNING→"planning"

        ``None`` (defensive — no status_to on the event) → ``"transition"``.
        """
        if status_to is None:
            return "transition"
        return str(getattr(status_to, "value", status_to)).lower()

    @staticmethod
    def _read_exec_error_origin(patch: TaskNodePatch) -> str | None:
        """Read the surfaced ``_exec_error_origin`` from the patch's
        ``extend_props_patch`` defensively. Returns ``None`` on any failure
        (incl. a hostile dict that raises on ``.get``) — the gate's
        origin read is decision-#14 trajectory-assembly scope (a raise here
        degrades to ``error_type=None``, NOT a gate failure)."""
        try:
            ep = patch.extend_props_patch
            if isinstance(ep, dict):
                origin = ep.get("_exec_error_origin")
                if isinstance(origin, str):
                    return origin
        except Exception:  # noqa: BLE001  trajectory-assembly read; 决策 #14 scope
            return None
        return None

    @staticmethod
    def _read_interface_error_code(patch: TaskNodePatch) -> str | None:
        """Read an optional ``interface_error_code`` surfaced on the patch's
        ``extend_props_patch`` (the bot may set it via ``result._ext_info``).
        Defensive — ``None`` on any failure / absent key."""
        try:
            ep = patch.extend_props_patch
            if isinstance(ep, dict):
                code = ep.get("interface_error_code")
                if code is not None:
                    return str(code)
        except Exception:  # noqa: BLE001  trajectory-assembly read; 决策 #14 scope
            return None
        return None

    def _read_exec_request_input_and_attempt(
        self, task_id: str, node_id: str
    ) -> tuple[str | None, int]:
        """Defensively read, in ONE graph query, the downstream request原文
        (``node.run_info.extend_props["_exec_request_input"]``) and the harness
        retries snapshot (``harness_retries``) for the EXECUTE/VERIFY trajectory
        event (REQ-1/REQ-5). ``action_input`` is the request原文 — **not
        truncated** — read from the node the executor would have surfaced it on
        (production surfacing is a separate executor-wire concern; the gate
        consumes the key defensively, ``None`` when absent). ``attempt`` mirrors
        ``_log_action``'s default ``harness_retries`` snapshot. Returns
        ``(None, 0)`` on any failure — decision-#14 trajectory-assembly scope
        (NOT the gate's main driving logic)."""
        try:
            graph = self._graph.query_task_dashboard(task_id)
            node = next((n for n in graph.tasks if n.node_id == node_id), None)
            if node is None:
                return None, 0
            ri = node.run_info.extend_props
            req = ri.get("_exec_request_input")
            req = req if isinstance(req, str) else None
            attempts = int(ri.get("harness_retries", 0) or 0)
            return req, attempts
        except Exception:  # noqa: BLE001  trajectory-assembly read; 决策 #14 scope
            return None, 0

    def _emit_execute_trajectory(
        self,
        patch: TaskNodePatch,
        result: NodeOpResult,
        *,
        action_type: str,            # "execute" | "verify"
        action_result: str,          # "failed" | "success" | "accept_pass" | "accept_fail"
        is_exec_error: bool = False,  # True only on the exec_error (EXECUTE err) path
    ) -> None:
        """REQ-5: fire one EXECUTE/VERIFY trajectory event **additively** to
        ``_log_action(NodeAction.EXECUTE/VERIFY, ...)`` (alongside, NOT replacing).

        ``error_type`` 按 ``patch.extend_props_patch["_exec_error_origin"]``
        经 ``_EXEC_ERROR_ORIGIN_TO_REASON`` 映射(``bot_interface→
        underlying_interface_error`` 等);未映射/无 origin → ``None``。
        ``error_msg`` = ``patch.exec_error`` 截断 ≤500(仅 ``is_exec_error`` 路径;
        EXECUTE(ok) / VERIFY 无 error_msg)。
        ``action_input`` = 下发请求原文(从节点 ``run_info.extend_props
        ["_exec_request_input"]`` 防御性读取;缺失 ``None``、**不截断**)。
        ``ext_info={"interface_error_code": <code>}`` 若 patch 透出该 code,否则 ``None``。
        ``status_from``/``status_to``/``attempt`` 对齐 ``_log_action``。

        决策 #14:全程 ``try/except`` 吞而不抛 + WARNING(轨迹旁路 fire-and-forget,
        不阻塞闸门主逻辑——本方法只在装配/发射轨迹,不在 swallows 内含任何
        驱动逻辑)。emitter(``emit_trajectory_event``)另兜一层 try/except + WARNING。
        ``repo is None`` 时 emitter 静默 no-op。
        """
        try:
            origin = self._read_exec_error_origin(patch)
            error_type = _EXEC_ERROR_ORIGIN_TO_REASON.get(origin) if origin else None
            if is_exec_error and patch.exec_error:
                raw = patch.exec_error
                error_msg = (
                    raw if len(raw) <= _EXEC_ERROR_MSG_MAX
                    else raw[: _EXEC_ERROR_MSG_MAX - 3] + "..."
                )
            else:
                error_msg = None
            action_input, attempt = self._read_exec_request_input_and_attempt(
                patch.task_id, patch.node_id
            )
            code = self._read_interface_error_code(patch)
            ext_info = {"interface_error_code": code} if code is not None else None
            self._log_trajectory(
                patch.task_id,
                patch.node_id,
                action_type,  # TrajectoryActionType.EXECUTE/VERIFY.value (emitter accepts str)
                action_result=action_result,
                action_input=action_input,
                error_type=error_type,
                error_msg=error_msg,
                ext_info=ext_info,
                status_from=result.prev_status,
                status_to=result.new_status,
                attempt=attempt,
            )
        except Exception as ex:  # noqa: BLE001  轨迹旁路:吞而不抛 + WARNING(决策 #14)
            logger.warning(
                "[task][trajectory][%s] task=%s node=%s 发射失败:%s",
                action_type, patch.task_id, patch.node_id, ex,
            )

    def _static_runtime(self, task_id: str):
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanRuntime
        cfg = self._graph._execution_config(task_id)
        # 判据:cfg 含 ``static_plan_id`` 或 ``static_plan_yaml`` 任一即视为预置模板 plan(不依赖 task_type 字符串);
        # task_type 仍可显式 STATIC_PLAN 兼容旧调用方,但默认 dynamic caller 经 execute 内容路由命中后,
        # 也会在此处回填 static_plan_id/static_plan_yaml 进入预置 plan runtime。
        template_id = cfg.get("static_plan_id")
        yaml_text = cfg.get("static_plan_yaml")
        if not template_id and not yaml_text and cfg.get("task_type") != "static_plan":
            return None
        if not yaml_text and template_id:
            # 显式只传 task_type/static_plan_id 未带 yaml → 从仓库 plans 懒加载。
            # plans 仓库固定位于 core/task/task_plan/plans — 本方法被 CentralizedExecutionAdapter
            # borrow 执行(bbs 重构自 task_center/task_service 同源迁入),锚定 static_plan 模块自身
            # 定位;沿用 parents[1] 会随宿主文件层级漂移(task_context/task_trajectory 下不再指向
            # core/task,模板懒加载静默失效 → runtime=None)。
            from pathlib import Path
            from agentclaw.community.core.task.task_plan import static_plan as _static_plan_module
            plans_dir = Path(_static_plan_module.__file__).resolve().parent / "plans"
            plans_path = plans_dir / f"{template_id}.yaml"
            if not plans_path.exists():
                return None
            yaml_text = plans_path.read_text(encoding="utf-8")
        try:
            definition = StaticPlanDefinition.from_yaml(
                str(yaml_text) if yaml_text else "",
                bindings=self._bot_bindings.bot_id_by_role if self._bot_bindings else None,
            )
            runtime = StaticPlanRuntime(definition, dict(cfg.get("template_input") or {}))
        except Exception:
            logger.exception(
                "[task][static-plan] runtime init failed task=%s template=%s",
                task_id,
                template_id,
            )
            raise
        logger.debug(
            "[task][static-plan] runtime loaded task=%s template=%s",
            task_id,
            template_id or definition.template_id,
        )
        return runtime
