from __future__ import annotations

from .router_test_support import (
    BotCandidate,
    NodeNotFoundError,
    NotFound,
    Status,
    TaskExecutionMode,
    TaskIntakeStatus,
    TaskReportResult,
    TaskReportStatus,
    TaskSourceType,
    TaskStateError,
    _body,
    _execution_trace,
    _raise,
    client,
    human_task_client,
    logging,
    pytest,
    task_client,
)


class TestRouter:
    def test_execute_logs_full_request_at_entry(self, task_client, caplog):
        client, svc = task_client
        request_body = {
            "task_spec": {
                "context": {
                    "title": "Execute request log",
                    "background": "Endpoint entrance diagnostics",
                    "extend_props": {"request_trace": "trace-endpoint"},
                },
                "goal": {
                    "objective": "exercise execute logging",
                    "acceptances": [{"id": "ac1", "acceptance": "log request"}],
                },
                "metadata": {
                    "title": "Execute request metadata",
                    "instruction": "log accepted boundary fields",
                },
            },
            "source_type": "bot",
            "owner_user_id": "owner-1",
            "owner_bot_id": "bot-1",
            "execution_config": {"task_type": "dynamic", "MAX_DEPTH": 2},
        }

        with caplog.at_level(logging.INFO, logger="start"):
            response = client.post("/api/v1/collaboration/tasks/execute", json=request_body)

        assert response.status_code == 200, response.text
        assert [call for call in svc.callback.calls if call[0] == "execute"]
        entry_log = next(
            record.getMessage()
            for record in caplog.records
            if record.name == "start" and "task_execute_internal" in record.getMessage()
        )
        assert "'request_trace': 'trace-endpoint'" in entry_log
        assert "'metadata': {'title': 'Execute request metadata'" in entry_log
        assert "'MAX_DEPTH': 2" in entry_log
        assert "'owner_user_id': 'owner-1'" in entry_log

    def test_new_relay_rejects_legacy_node_write(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/nodes/update",
            json={"task_id": "t1", "node_id": "t1", "status": "SUCCESS"},
        )
        assert response.status_code != 200
        assert svc.callback.calls == []

    def test_relay_context_uses_network_admission_and_execution_generation(
        self, task_client
    ):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)

        response = client.get(
            "/api/v1/collaboration/tasks/t1/nodes/t1/context",
            headers={"X-Relay-Token": "run-1"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["node_id"] == "t1"

    def test_relay_bbs_claim_trusts_internal_declared_bot_id(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "bbs-step", Status.PENDING)
        response = client.post(
            "/api/v1/collaboration/tasks/bbs/claim",
            json={"task_id": "t1", "node_id": "bbs-step", "bot_id": "another-bot"},
        )
        assert response.status_code == 200, response.text
        assert svc.callback.calls[-1] == (
            "bbs_claim",
            ("t1", "another-bot", "bbs-step", None),
        )

    def test_relay_search_uses_internal_network_and_generation(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)

        response = client.post(
            "/api/v1/collaboration/tasks/search",
            json={
                "query": "需要测试能力",
                "task_id": "t1",
                "node_id": "t1",
                "relay_token": "run-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {
            "candidates": [
                {
                    "bot_id": "bot-1",
                    "name": "Risk Bot",
                    "description": "risk analysis",
                    "score": 0.91,
                },
            ],
        }
        assert svc.callback.calls[-1] == (
            "search_bots",
            ("需要测试能力", "t1", "t1", "run-1"),
        )

    def test_relay_search_ignores_unknown_fields(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        before = len(svc.callback.calls)
        response = client.post(
            "/api/v1/collaboration/tasks/search",
            json={
                "query": "需要测试能力",
                "task_id": "t1",
                "node_id": "t1",
                "relay_token": "run-1",
                "extra": "x",
            },
        )

        assert response.status_code == 200, response.text
        assert len(svc.callback.calls) == before + 1

        assert svc.callback.calls[-1] == ("search_bots", ("需要测试能力", "t1", "t1", "run-1"))

    def test_relay_dispatch_uses_internal_network_and_origin_generation(
        self, task_client
    ):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "origin", Status.RUNNING)
        svc.set_node_status("t1", "target", Status.PENDING)
        svc.set_node_planned_by("t1", "target", "origin")

        response = client.post(
            "/api/v1/collaboration/tasks/dispatch",
            json={
                "task_id": "t1",
                "origin_node_id": "origin",
                "target_node_id": "target",
                "relay_token": "run-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"ok": True, "node_id": "target"}
        assert svc.callback.calls[-1] == ("dispatch_node", ("t1", "target"))

    def test_relay_dispatch_ignores_unknown_fields(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "origin", Status.RUNNING)
        svc.set_node_status("t1", "target", Status.PENDING)
        svc.set_node_planned_by("t1", "target", "origin")
        before = len(svc.callback.calls)

        response = client.post(
            "/api/v1/collaboration/tasks/dispatch",
            json={
                "task_id": "t1",
                "origin_node_id": "origin",
                "target_node_id": "target",
                "relay_token": "run-1",
                "extra": "x",
            },
        )

        assert response.status_code == 200, response.text
        assert len(svc.callback.calls) == before + 1

        assert svc.callback.calls[-1] == ("dispatch_node", ("t1", "target"))

    def test_new_relay_report_requires_current_execution_generation(
        self,
        task_client,
    ):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        body = {
            "task_id": "t1",
            "node_id": "t1",
            "event_type": "EXECUTION_RESULT",
            "event_id": "event-1",
            "relay_token": "run-1",
            "trace": _execution_trace(),
            "event": {"execution_decision": "DECLINED"},
        }
        response = client.post("/api/v1/collaboration/tasks/callback/report", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "APPLIED"
        report_calls = [
            item for item in svc.callback.calls if item[0] == "new_relay_report"
        ]
        assert report_calls[-1][1].event_id == "event-1"

        rejected = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={**body, "event_id": "event-2", "relay_token": "old-run"},
        )
        assert rejected.status_code != 200
        assert (
            len([item for item in svc.callback.calls if item[0] == "new_relay_report"])
            == 1
        )

    def test_new_relay_report_validation_failure_answers_422(self, task_client):
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "PLAN_RESULT",
                "event_id": "event-1",
                "relay_token": "run-1",
                "event": None,
                "task_acceptance_result": {
                    "verdict": "PASS",
                    "done_items": [],
                    "gap_items": [],
                },
            },
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == 422000
        # 该用例的 422 来自非法 AcceptanceResult;trace 缺失不再参与判定。
        # 校验失败也落 task_callback 审计:best-effort 字段 + 脱敏原文 + exec_error
        ingests = [c for c in svc.callback.calls if c[0] == "ingest"]
        assert len(ingests) == 1, "422 校验失败仍应落一条 relay 审计行"
        payload = ingests[0][1].data
        assert payload["result"]["success"] is False
        assert "relay_token" not in str(payload["_raw_callback_body"].get("relay_token")) \
            or payload["_raw_callback_body"]["relay_token"] == "***"
        assert payload["result"]["exec_error"]

    def test_new_relay_report_names_missing_steps_and_step_fields(self, task_client):
        """/callback/report relay 分支的 422 必须把问题点到具体字段路径:
        trace.steps 缺失 / 单个 step 缺七字段之一 / action_type 非枚举。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        base = {
            "task_id": "t1", "node_id": "t1", "event_type": "EXECUTION_RESULT",
            "event_id": "event-v", "relay_token": "run-1",
            "event": {"execution_decision": "DECLINED"},
        }
        full_steps = _execution_trace()["steps"]
        full_step = full_steps[0]

        # ① trace 缺 steps → 点名 trace.steps
        r1 = client.post("/api/v1/collaboration/tasks/callback/report",
                        json={**base, "trace": {}})
        assert r1.status_code == 422
        assert "trace.steps" in r1.json()["message"], r1.json()["message"]

        # ② step 缺 boost_reason → 点名 trace.steps[0].boost_reason
        step_missing = {k: v for k, v in full_step.items() if k != "boost_reason"}
        steps_missing = _execution_trace()["steps"]
        steps_missing[0] = step_missing
        r2 = client.post("/api/v1/collaboration/tasks/callback/report",
                        json={**base, "trace": {"steps": steps_missing}})
        assert r2.status_code == 422
        msg2 = r2.json()["message"]
        assert "boost_reason" in msg2 and "steps[0]" in msg2, msg2

        # ③ 空 action_type → 点名 action_type(S 码主事件标识合法值,不再限枚举)
        steps_bad_type = _execution_trace()["steps"]
        steps_bad_type[0] = {**full_step, "action_type": ""}
        r3 = client.post("/api/v1/collaboration/tasks/callback/report",
                        json={**base, "trace": {"steps": steps_bad_type}})
        assert r3.status_code == 422
        msg3 = r3.json()["message"]
        assert "action_type" in msg3, msg3

    def test_new_relay_report_persists_callback_audit_on_success(self, task_client):
        """成功上报:落一条 task_callback 审计行(invoker=relay,run_id=event_id+node_id
        为幂等 upsert 键,orig 原文脱敏 relay_token,result.success=True)。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-1",
                "relay_token": "run-1",
                "trace": _execution_trace(),
                "event": {"execution_decision": "DECLINED"},
            },
        )
        assert response.status_code == 200, response.text

        ingests = [c for c in svc.callback.calls if c[0] == "ingest"]
        assert len(ingests) == 1, "成功上报应恰好落一条审计行"
        payload = ingests[0][1].data
        # 幂等身份:(run_id=event_id, node_id) —— 经 loop_task_id 表达
        assert payload["loop_task_id"] == "event-1::t1"
        assert payload["node_id"] == "t1"
        assert payload["event_id"] == "event-1"
        assert payload["workflow_source"] == "relay"
        assert payload["status"] == "EXECUTION_RESULT"
        assert payload["result"]["success"] is True
        assert payload["result"]["exec_error"] is None
        # 原始报文留底且 token 脱敏
        raw = payload["_raw_callback_body"]
        assert raw["event_id"] == "event-1"
        assert raw["relay_token"] == "***"
        assert payload["result"]["_ext_info"]["task_id"] == "t1"

    def test_new_relay_report_rejection_emits_system_trajectory_row(self, task_client):
        """被拒上报(token 过期)→ 轨迹事件表落一条 system 类型事件:
        action_type=system、error_msg=异常信息、ext_info含完整堆栈与
        phase/event_id;原异常照常外抛(响应非 200)。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        # trace 在线上契约可选;拒绝场景走 token 校验,不带 trace 最小化 body
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-stale",
                "relay_token": "stale-run",
                "event": {"execution_decision": "DECLINED"},
            },
        )
        assert response.status_code != 200, "拒绝照常外抛"

        emitter = svc._task_context_service
        assert len(emitter.emit_calls) == 1, "应恰落一条 system 轨迹行"
        task_id, node_id, action_type, kwargs = emitter.emit_calls[0]
        assert (task_id, node_id, action_type) == ("t1", "t1", "system")
        assert "expired" in kwargs["error_msg"], kwargs["error_msg"]
        ext = kwargs["ext_info"]
        assert ext["phase"] == "callback_report"
        assert ext["relay_event_id"] == "event-stale"
        assert "TaskStateError" in ext["stack"], "堆栈应记录异常类型"
        assert "require_relay_attempt" in ext["stack"] or "Traceback" in ext[
            "stack"], f"堆栈应含调用栈: {ext['stack'][:200]}"

    def test_new_relay_report_persists_callback_audit_on_rejection(
        self, task_client
    ):
        """被拒上报(token 过期):审计照落——success=False + exec_error 携带拒绝原因,
        原异常仍按原路径抛出(响应非 200)。"""
        client, svc = task_client
        svc.set_execution_config(
            {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
        )
        svc.set_node_status("t1", "t1", Status.RUNNING)
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "t1",
                "event_type": "EXECUTION_RESULT",
                "event_id": "event-stale",
                "relay_token": "stale-run",
                "trace": _execution_trace(),
                "event": {"execution_decision": "DECLINED"},
            },
        )
        assert response.status_code != 200, "token 过期应被拒(原异常外抛)"

        ingests = [c for c in svc.callback.calls if c[0] == "ingest"]
        assert len(ingests) == 1, "被拒上报也应落审计行"
        payload = ingests[0][1].data
        assert payload["loop_task_id"] == "event-stale::t1"
        assert payload["result"]["success"] is False
        assert "expired" in str(payload["result"]["exec_error"])
        assert payload["_raw_callback_body"]["relay_token"] == "***"

    def test_relay_report_audit_payload_to_callback_record(self):
        """payload → ``_to_callback_record`` 落库形态:run_id=event_id/node_id 正确、
        invoker=relay、orig 为脱敏原文 JSON、endpoint 不经 HTTP 直接验证翻译层。"""
        from agentclaw.community.adapters.http.task.callback_routes import (
            relay_report_audit_payload,
        )
        from agentclaw.community.adapters.http.task.schemas_relay_report import (
            TaskReportCommandDTO,
        )
        from agentclaw.community.core.task.task_runner.callback_adapter import (
            _to_callback_record,
        )
        import json as _json

        raw = {
            "task_id": "t1",
            "node_id": "n1",
            "event_type": "EXECUTION_RESULT",
            "event_id": "ev-9",
            "relay_token": "secret-token",
            "trace": _execution_trace(),
            "event": {"execution_decision": "DECLINED"},
        }
        command = TaskReportCommandDTO.model_validate(raw).to_domain()
        payload = relay_report_audit_payload(
            raw, command=command, result=TaskReportResult(TaskReportStatus.APPLIED)
        )
        record = _to_callback_record(payload, event_id="ev-9", process_status=None)

        assert record.run_id == "ev-9"
        assert record.node_id == "n1"
        assert record.invoker == "relay"
        assert record.status == "EXECUTION_RESULT"
        assert record.result_success is True
        assert record.event_id == "ev-9"
        assert record.extend_props == {
            "relay_report": True,
            "task_id": "t1",
            "event_type": "EXECUTION_RESULT",
            "execution_decision": "DECLINED",
        }
        # orig = 脱敏原文(secret token 不进库)
        orig = _json.loads(record.orig_callback_data)
        assert orig["relay_token"] == "***"
        assert orig["event_id"] == "ev-9"

    def test_intake_uses_authenticated_owner_and_server_session_header(
        self, human_task_client
    ):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/intake",
            headers={"X-Task-Intake-Session-ID": "tis-1"},
            json={"action": "CONTINUE", "message": "补充验收", "owner_user_id": "attacker"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["session_id"] == "tis-1"
        _, (command, context) = svc.callback.calls[0]
        assert command.action.value == "CONTINUE"
        assert context.session_id == "tis-1"
        assert context.owner_user_id == "owner-1"

    def test_intake_accepts_mode_and_a2a_proxy_request(self, human_task_client):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/intake",
            json={
                "action": "START",
                "message": "分析支付失败率",
                "requested_a2a_proxy_bot_ref": "proxy",
                "requested_target": {"target_type": "BOT", "target_id": "risk:owner-2"},
                "requested_mode": "centralized",
            },
        )
        assert response.status_code == 200, response.text
        _, (command, context) = svc.callback.calls[0]
        assert command.requested_a2a_proxy_bot_ref == "proxy"
        assert command.requested_target.target_id == "risk:owner-2"
        assert command.requested_mode == TaskExecutionMode.CENTRALIZED
        assert context.source_type == TaskSourceType.API

    def test_intake_exposes_processing_without_run_credentials(self, human_task_client):
        client, svc = human_task_client
        result = svc._confirmed_intake()
        result.status = TaskIntakeStatus.CLARIFYING
        result.a2a_run_id = "run-secret"
        svc.intake_result = result
        response = client.post(
            "/api/v1/collaboration/tasks/intake",
            json={"action": "RESUME"},
            headers={"X-Task-Intake-Session-ID": "tis-1"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["processing"] is True
        assert "a2a_run_id" not in data

    def test_start_uses_only_confirmed_session_to_build_request(
        self, human_task_client
    ):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/start",
            json={"session_id": "tis-1"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["task_id"] == "task-1"
        assert response.json()["data"]["node_id"] == "task-1"
        assert svc.callback.calls[0] == ("get_confirmed_intake", ("tis-1", "owner-1"))
        _, request = svc.callback.calls[1]
        assert request.owner_user_id == "owner-1"
        assert request.task_spec.goal.objective == "上线模型"
        assert request.execution_config["intake_session_id"] == "tis-1"

    def test_start_ignores_client_supplied_owner_fields(self, human_task_client):
        client, svc = human_task_client
        response = client.post(
            "/api/v1/collaboration/tasks/start",
            json={"session_id": "tis-1", "owner_user_id": "attacker"},
        )
        assert response.status_code == 200, response.text
        assert svc.callback.calls[0] == ("get_confirmed_intake", ("tis-1", "owner-1"))
        assert svc.callback.calls[1][1].owner_user_id == "owner-1"

    def test_get_task_returns_dashboard(self, human_task_client):
        client, svc = human_task_client
        svc.set_node_status("task-1", "task-1", Status.RUNNING)
        response = client.get("/api/v1/collaboration/tasks/task-1")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["tasks"][0]["task_id"] == "task-1"

    def test_legacy_callback_rejected_for_relay_task(self, task_client):
        client, svc = task_client
        svc.set_execution_config({"orchestration_mode": "relay"})
        response = client.post(
            "/api/v1/collaboration/tasks/callback/report",
            json={
                "task_id": "t1",
                "node_id": "c1",
                "status": "SUCCESS",
                "output": "误用旧协议直接完成",
                "acceptance_result": {
                    "verdict": "DONE",
                    "done_items": [],
                    "gap_items": [],
                },
                "extend_props": {},
            },
        )
        assert response.status_code == 409, response.text

    def test_task_level_legacy_callback_rejected_for_relay_task(self, client):
        client, svc = client
        svc.set_execution_config({"orchestration_mode": "relay"})
        response = client.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(task_id="t1", status="COMPLETED", is_success=True),
        )
        assert response.status_code == 409, response.text

    def test_workflow_result_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "result"

    def test_node_result_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_result", json=_body(node=True)
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][1].data["loop_task_id"] == "t1::c1"

    def test_workflow_start_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_start",
            json=_body(loop_task_id="t1::root1", status="RUNNING"),
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "start"

    def test_node_start_success(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_start",
            json=_body(node=True, status="RUNNING"),
        )
        assert r.status_code == 200, r.text
        assert svc.callback.calls[0][0] == "start"

    def test_result_idempotent_when_already_terminal(self, client):
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("DONE->DONE"))
        svc.set_node_status("t1", "root1", Status.DONE)
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 200, r.text  # 幂等 ack

    def test_result_409_when_illegal(self, client):
        # 非终态重投:report_result 抛 TaskStateError,非幂等 → 不进幂等分支。
        # ``@envelope_errors`` 经 ``ENVELOPE_ERRORS`` 把它映射为 409 ErrorEnvelope,
        # 该映射在 handler 帧内完成,不依赖本 fixture 的中央 handler。
        c, svc = client
        svc.callback.report_result = _raise(TaskStateError("PENDING->DONE"))
        svc.set_node_status("t1", "root1", Status.PENDING)  # 非终态
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == 409000

    def test_start_409_on_stale(self, client):
        # start 抛 TaskStateError;disposition!=result 不进幂等分支 → 409 ErrorEnvelope。
        c, svc = client
        svc.callback.start_run = _raise(TaskStateError("stale"))
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_start",
            json=_body(node=True, status="RUNNING"),
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == 409000

    def test_not_found_404(self, client):
        # report_result 抛 NodeNotFoundError → ``ENVELOPE_ERRORS`` 映射为 404 ErrorEnvelope。
        c, svc = client
        svc.callback.report_result = _raise(NodeNotFoundError("x"))
        r = c.post(
            "/api/v1/collaboration/tasks/callback/workflow_result",
            json=_body(loop_task_id="t1::root1"),
        )
        assert r.status_code == 404, r.text
        assert r.json()["code"] == 404000

    def test_correlation_error_400(self, client):
        # task 级无回声 + 空 registry → translate 抛 NotFound(core.errors;原计划 CallbackCorrelationError
        # 尚未落地)。未被 @envelope_errors 映射、本 fixture 无中央 handler → 异常经 TestClient 上抛
        # → 断言领域错误上抛。
        c, _ = client
        with pytest.raises(NotFound):
            c.post(
                "/api/v1/collaboration/tasks/callback/workflow_result", json=_body()
            )  # 无 loop_task_id,registry 空

    def test_validation_422(self, client):
        c, _ = client
        r = c.post(
            "/api/v1/collaboration/tasks/callback/node_result", json={"task_id": "t1"}
        )  # 缺必填
        assert r.status_code == 422, r.text

    def test_claw_mind_callback_ingests_only(self, client):
        # ClawMind HttpCallbackPayload → 只落库(ingest),不推进引擎(不走 start/result)
        c, svc = client
        body = {
            "workflow_id": "wf-1",
            "flow_id": "fl-1",
            "status": "succeeded",
            "ext_info": {
                "flow_runs": {"status": "succeeded", "origin_session_id": "S-9"},
                "node_executions": [],
            },
        }
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
        assert r.status_code == 200, r.text
        assert svc.callback.calls and svc.callback.calls[0][0] == "ingest"
        assert all(k == "ingest" for k, _ in svc.callback.calls)

    def test_bcn_callback_ingests_only(self, client):
        # BCN CloudEvent(已处理事件)→ 只落库(ingest),不推进引擎
        c, svc = client
        evt = {
            "spec_version": "1.0",
            "event_id": "e1",
            "event_type": "state_machine.node.completed",
            "source": "bcs",
            "scope": {"group_id": "g1", "session_id": "s1", "run_id": "r1"},
            "stream": {"key": "k", "sequence": 1},
            "actor": {"type": "bot", "id": "b"},
            "data": {
                "run_id": "r1",
                "node_id": "n1",
                "outcome": "success",
                "output": {"x": 1},
            },
        }
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=evt)
        assert r.status_code == 200, r.text
        assert svc.callback.calls and svc.callback.calls[0][0] == "ingest"
        assert all(k == "ingest" for k, _ in svc.callback.calls)

    def test_bcn_unhandled_event_acks_without_ingest(self, client):
        c, svc = client
        evt = {
            "spec_version": "1.0",
            "event_id": "e2",
            "event_type": "message.created",
            "source": "bcs",
            "scope": {"group_id": "g1", "session_id": "s1"},
            "stream": {"key": "k", "sequence": 1},
            "actor": {},
            "data": {},
        }
        r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=evt)
        assert r.status_code == 200, r.text
        assert svc.callback.calls == []  # 非处理事件:不落库、不推进


@pytest.mark.unit
def test_relay_report_with_malformed_artifacts_gets_4xx_envelope(task_client):
    """评审修正:artifact 解析失败(基 ValueError)必须落 4xx 错误信封,不得 500。

    2026-10-22 契约调整:artifacts 位于报文顶层(与 trace 平级),content 字段收 JSON 对象。
    本例给出**非对象可解析**的携带方式之一:file 分支缺 file_type(构造器非空校验失败)。
    """

    client, svc = task_client
    body = {
        "task_id": "task-1",
        "node_id": "task-1",
        "event_id": "exec-artifact-bad",
        "relay_token": "run-1",
        "event_type": "EXECUTION_RESULT",
        "artifacts": [
            {
                "artifact_kind": "file",
                "session_id": "sess-9",
                "task_id": "task-1",
                "node_id": "task-1",
                "attempt": 0,
                "text_content": None,
                "file_content": {
                    "storage_type": "bot_workspace",
                    "storage_id": "reports/x.md",
                    "file_name": "x.md",
                    "file_id": None,
                },  # 缺 file_type → 分支构造器 fail-loud
                "url_link_content": None,
                "code_content": None,
            }
        ],
        "event": {
            "execution_decision": "ACCEPTED",
            "actual_goal": {"objective": "o", "acceptances": []},
            "local_acceptance_result": {"verdict": "DONE", "done_items": [], "gap_items": []},
            "output_patch": {},
        },
    }
    r = client.post("/api/v1/collaboration/tasks/callback/report", json=body)
    assert r.status_code != 500, "malformed artifact report must not surface as 500"
    assert 400 <= r.status_code < 500
    assert "cache_id" not in r.text  # 保守:不回显原文 payload 的键


@pytest.mark.unit
def test_relay_report_declined_with_artifacts_rejected(task_client):
    """DECLINED + 顶层 artifacts → TaskStateError → 409,不落库。"""

    client, _service = task_client
    body = {
        "task_id": "task-1",
        "node_id": "task-1",
        "event_id": "exec-artifact-declined",
        "relay_token": "run-1",
        "event_type": "EXECUTION_RESULT",
        "artifacts": [
            {
                "artifact_name": "不应携带的文本",
                "artifact_kind": "text",
                "task_id": "task-1",
                "node_id": "task-1",
                "attempt": 0,
                "text_content": {"text": "hi"},
                "file_content": None,
                "url_link_content": None,
                "code_content": None,
            }
        ],
        "event": {
            "execution_decision": "DECLINED",
            "output_patch": {},
        },
    }
    r = client.post("/api/v1/collaboration/tasks/callback/report", json=body)
    assert r.status_code == 409, r.text


@pytest.mark.unit
def test_task_artifact_content_error_maps_to_4xx():
    """评审修正:artifacts 契约违规(attempt 失配等)落 422,不得落 TaskError 500 兜底。"""

    from agentclaw.community.adapters.http.openapi_v1.responses import (
        mapped_error_response,
    )
    from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
    from fastapi import Request

    scope = {"type": "http", "path": "/", "headers": [], "method": "POST", "query_string": b""}
    request = Request(scope)
    response = mapped_error_response(TaskArtifactContentError("attempt mismatch"), request)
    assert response is not None, "TaskArtifactContentError 必须在信封映射表中"
    assert response.status_code == 422
    assert b"artifact" in response.body or response.status_code == 422


@pytest.mark.parametrize(
    "viewer_type,viewer_id", [("bot", "bot:owner"), ("human", "146836")]
)
def test_relay_search_forwards_explicit_viewer_after_generation_check(
    task_client, monkeypatch, viewer_type, viewer_id
):
    client, svc = task_client
    svc.set_execution_config(
        {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
    )
    svc.set_node_status("t1", "t1", Status.RUNNING)
    calls = []

    async def search_bots(**kwargs):
        calls.append(kwargs)
        return [
            BotCandidate(
                bot_id="candidate:owner",
                name="Candidate",
                description="match",
                score=0.9,
            )
        ]

    monkeypatch.setattr(svc, "search_bots", search_bots)
    body = {
        "query": "q",
        "task_id": "t1",
        "node_id": "t1",
        "relay_token": "run-1",
        "viewer_actor_type": viewer_type,
        "viewer_actor_id": viewer_id,
    }
    stale = client.post(
        "/api/v1/collaboration/tasks/search", json={**body, "relay_token": "stale"}
    )
    assert stale.status_code == 409
    assert not calls
    response = client.post("/api/v1/collaboration/tasks/search", json=body)
    assert response.status_code == 200, response.text
    assert calls == [
        {
            "query": "q",
            "task_id": "t1",
            "node_id": "t1",
            "relay_token": "run-1",
            "viewer_actor_type": viewer_type,
            "viewer_actor_id": viewer_id,
        }
    ]
    assert response.json()["data"] == {
        "candidates": [
            {
                "bot_id": "candidate:owner",
                "name": "Candidate",
                "description": "match",
                "score": 0.9,
            }
        ]
    }


@pytest.mark.parametrize(
    "viewer",
    [
        {"viewer_actor_type": "bot"},
        {"viewer_actor_id": "bot:owner"},
        {"viewer_actor_type": "admin", "viewer_actor_id": "bot:owner"},
        {"viewer_actor_type": "human", "viewer_actor_id": " "},
    ],
)
def test_relay_search_invalid_viewer_never_calls_service(task_client, viewer):
    client, svc = task_client
    before = len(svc.callback.calls)
    response = client.post(
        "/api/v1/collaboration/tasks/search",
        json={
            "query": "q",
            "task_id": "t1",
            "node_id": "t1",
            "relay_token": "run-1",
            **viewer,
        },
    )
    assert response.status_code == 422
    assert "run-1" not in response.text
    assert len(svc.callback.calls) == before


def test_relay_search_null_viewer_keeps_legacy_service_call(task_client):
    client, svc = task_client
    svc.set_execution_config(
        {"orchestration_mode": "relay", "intake_session_id": "tis-1"}
    )
    svc.set_node_status("t1", "t1", Status.RUNNING)
    response = client.post(
        "/api/v1/collaboration/tasks/search",
        json={
            "query": "q",
            "task_id": "t1",
            "node_id": "t1",
            "relay_token": "run-1",
            "viewer_actor_type": None,
            "viewer_actor_id": None,
        },
    )
    assert response.status_code == 200, response.text
    assert svc.callback.calls[-1] == ("search_bots", ("q", "t1", "t1", "run-1"))
