"""Durable phase submissions and bounded recovery; all BCS facts are read via CLI.

The API object supplies the referee's existing state/CLI operations. No token,
artifact or raw server error is included in the safe status projection.
"""
from contextlib import contextmanager
import fcntl
import json
import os
import uuid

MAX_RETRIES = 2
RUNNING = {"SPEAK_RUNNING": "speak", "PK_SPEAK_RUNNING": "speak",
           "VOTE_RUNNING": "vote", "PK_VOTE_RUNNING": "vote"}


def scope(state, kind):
    return f"{state['round']}:{'pk' if state['phase'].startswith('PK_') or state['phase'].startswith('AWAIT_PK_') else 'regular'}:{kind}"


def safe_status(state):
    saved = state.get("recovery", {})
    current = saved.get("current")
    return {"current_submission": current,
            "retry_limit": MAX_RETRIES,
            "retries": saved.get("retries", {}),
            "processed_failure_run_ids": sorted(saved.get("processed", {})),
            "stopped": saved.get("stopped")}


class Recovery:
    def __init__(self, api):
        self.api = api

    @contextmanager
    def serial(self, session):
        # This separate lock serializes full open/reconcile operations, including
        # network uncertainty. State writes still use the existing state lock.
        fd = os.open(str(self.api.state_path(session).with_suffix('.submission.lock')), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def facts(self, state, run_id):
        code, out, _ = self.api.bcs_cli("--json", "collaborate", "query", "--run", run_id, "--result")
        view = self.api.last_json(out)
        run = view or {}
        if code or run.get("run_id") != run_id or run.get("session_id") != state["session_id"] \
                or run.get("group_id") != state["group_id"] or run.get("created_by") != state["referee_uuid"]:
            self.api.die("RUN_FACTS_UNAVAILABLE", "无法核实本局主持人提交的运行，停止恢复；请维护者核对服务端事实。")
        return run, run.get("terminal_result")

    def check(self, session, failed_run):
        state = self.api.load_state(session)
        recovery = state.get("recovery", {})
        current = recovery.get("current")
        run_id = failed_run or (current or {}).get("run_id")
        if not run_id:
            self.api.die("SUBMISSION_UNCERTAIN", "没有可核验的运行 ID，不能盲目重开。请维护者核对服务端运行。")
        run, terminal = self.facts(state, run_id)
        # A lost submission response can only be reconciled against the nonce
        # persisted before sending and echoed in the server's original input.
        if current and current["state"] == "uncertain":
            marker = run.get("client_reference")
            if not marker or marker != current.get("correlation", {}).get("nonce"):
                self.api.die("SUBMISSION_UNCERTAIN", "运行不匹配未确认提交，停止；不能再次盲目提交。")
            with self.api.locked(session):
                state = self.api.load_state(session)
                current = state["recovery"]["current"]
                current.update(run_id=run_id, state="submitted")
                self.api.save_state(state)
            recovery = state["recovery"]
        if run_id in recovery.get("processed", {}):
            return {"action": "duplicate", "failed_run_id": run_id, "phase": state["phase"]}
        if not current or current.get("run_id") != run_id:
            return {"action": "stale", "failed_run_id": run_id, "phase": state["phase"]}
        if run["status"] not in ("failed", "aborted"):
            self.api.die("RUN_NOT_TERMINAL", "该运行仍未失败或中止，不能重开；继续等待。")
        if not isinstance(terminal, dict) or terminal.get("run_id") != run_id \
                or terminal.get("status") != run["status"] or terminal.get("schema_version") != 1:
            self.api.die("RUN_RESULT_UNAVAILABLE", "服务端缺少可信终态摘要，停止自动恢复。")
        reason = terminal.get("reason_category")
        if run["status"] == "aborted" or reason == "user_aborted" or reason is None:
            with self.api.locked(session):
                state = self.api.load_state(session)
                state["recovery"]["stopped"] = {"run_id": run_id, "reason": reason or "scope_invalid"}
                self.api.save_state(state)
            self.api.die("RECOVERY_STOPPED", "运行已中止或范围失效，不自动重开。")
        if reason not in ("node_failed", "human_timeout", "startup_failed", "publication_failed"):
            self.api.die("RUN_RESULT_UNAVAILABLE", "未知终态原因，停止自动恢复。")
        phase = state["phase"]
        current_scope = scope(state, current["kind"])
        if current["scope"] != current_scope:
            return {"action": "stale", "failed_run_id": run_id, "phase": phase}
        if RUNNING.get(phase) == current["kind"]:
            count = recovery.get("retries", {}).get(current["scope"], 0)
            if count >= MAX_RETRIES:
                self.api.die("RECOVERY_EXHAUSTED", "本阶段已自动重开两次，停止恢复。请维护者排障或新建会话。", retries=count)
            action = "retry-" + current["kind"]
        elif current["kind"] == "speak" and phase in ("AWAIT_VOTE_START", "AWAIT_PK_VOTE_START"):
            action = "open-vote"
        else:
            action = "follow-status"
        return {"action": action, "failed_run_id": run_id, "phase": phase}

    def inspect(self, session, failed_run):
        with self.serial(session):
            return self.check(session, failed_run)

    def open(self, args, kind):
        api = self.api
        session = args.session
        failed_run = getattr(args, "failed_run", None)
        with self.serial(session):
            state = api.load_state(session)
            current = state.get("recovery", {}).get("current")
            recovery_action = None
            if args.retry or failed_run:
                recovery_action = self.check(session, failed_run)
                if recovery_action["action"] in ("duplicate", "stale", "follow-status"):
                    api.emit({**recovery_action, "submitted": False, "next_action": "按当前 status 交接；旧结果不重开。"}, compact=True)
                    return
                expected = "retry-" + kind if args.retry else "open-" + kind
                if recovery_action["action"] != expected:
                    api.die("WRONG_RECOVERY_ACTION", "当前事实不允许这个恢复动作；先查 status，不重做已落盘阶段。")
                failed_run = recovery_action["failed_run_id"]
                state = api.load_state(session)
            elif current and current["state"] == "uncertain":
                # Query the service's permission fact, but absence of a unique
                # Run identity is never evidence that the request was not sent.
                api.bcs_cli("collaborate", "permission", "--session", session)
                api.die("SUBMISSION_UNCERTAIN", "上次提交尚未确认，不能再次提交。请维护者核对服务端运行 ID。")
            if state.get("recovery", {}).get("stopped"):
                api.die("RECOVERY_STOPPED", "本局自动恢复已停止，请维护者排障或新建会话。")
            busy = None if args.retry else api.self_lock_hint(session, "AWAIT_NEXT_ROUND" if kind == "speak" else "AWAIT_VOTE_START")
            api.require_run_slot(session, busy)
            # Reserve before rendering too: a crash between phase mutation and
            # submission cannot leave an apparently safe new submission slot.
            nonce = uuid.uuid4().hex
            with api.locked(session):
                state = api.load_state(session)
                saved = state.setdefault("recovery", {})
                saved["current"] = {"state": "uncertain", "run_id": None, "kind": kind, "scope": scope(state, kind),
                                    "attempt": None, "correlation": {"nonce": nonce}}
                api.save_state(state)
            prepare = api.prepare_speak_run if kind == "speak" else api.prepare_vote_run
            payload = prepare(session, args.retry)
            correlation = {"nonce": nonce, "round": payload["round"],
                           "stage": "pk" if payload["phase"].startswith("PK_") else "regular",
                           "kind": kind, "attempt": payload["attempt"]}
            with api.locked(session):
                state = api.load_state(session)
                saved = state["recovery"]
                key = scope(state, kind)
                saved["current"].update(scope=key, attempt=payload["attempt"], correlation=correlation)
                if failed_run:
                    saved.setdefault("processed", {})[failed_run] = nonce
                if args.retry:
                    saved.setdefault("retries", {})[key] = saved.get("retries", {}).get(key, 0) + 1
                api.save_state(state)
            with open(payload["input_path"], encoding="utf-8") as source:
                data = json.load(source)
            data["bcs_client_reference"] = nonce
            with open(payload["input_path"], "w", encoding="utf-8") as target:
                json.dump(data, target, ensure_ascii=False)
            # Uncertainty is persisted before the external request. Any failure,
            # timeout, malformed response or process death leaves it in place.
            result = api.submit_run(session, payload["yaml_path"], payload["input_path"],
                                    payload["panel_params_path"], payload["panel_tab"], payload["bindings"])
            run_id = result.get("run_id") or result.get("run", {}).get("run_id") or (result.get("nodes") or [{}])[0].get("run_id")
            if not isinstance(run_id, str) or not run_id:
                api.die("SUBMISSION_UNCERTAIN", "提交响应没有运行 ID，停止；先核对服务端事实，不能重复提交。")
            with api.locked(session):
                state = api.load_state(session)
                state["recovery"]["current"].update(state="submitted", run_id=run_id)
                api.save_state(state)
            api.emit({"phase": payload["phase"], "round": payload["round"], "attempt": payload["attempt"],
                      "submitted": True, **({"announcement": payload["announcement"]} if kind == "speak" else {}), "run_id": run_id,
                      "next_action": api.SUBMITTED_NEXT_ACTION if kind == "speak" else
                      "立刻结束激活。副屏显示开投提示和旧票作废说明；不追加工具调用，等待独立计票节点。"}, compact=True)
