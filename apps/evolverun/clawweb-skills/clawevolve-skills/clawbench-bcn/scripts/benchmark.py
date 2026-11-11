"""P1 local-only group benchmark entry point. No ClawWeb callbacks or uploads."""
import argparse
import json
import os
import signal
import time
import uuid
from pathlib import Path
from bcn_adapter import BcnAdapter
from evidence import collect_member
from grading_adapter import code_grade, llm_grade, combine
from runtime import load_runtime, validate_profile, Singlebox
from task_loader import load_case

def emit_progress(message, log_path=None):
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write(line + "\n")

def write(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)

def run(case, config, source):
    os.umask(0o077)
    directory = config["output_dir"] / (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:10])
    directory.mkdir(parents=True, mode=0o700)
    # Run-scoped logger: no raw subprocess/model output or configuration values.
    def progress(message):
        emit_progress(message, directory / "runner.log")
    progress(f"运行目录: {directory}")
    artifacts, evidence = directory / "artifacts", directory / "evidence"
    artifacts.mkdir(); evidence.mkdir()
    info = {"run_id": directory.name, "case_id": case["meta"]["id"], "strategy": "manager_worker", "started_at": time.time()}
    result = {"execution_status": "environment_error", "grading_status": "skipped", "score": None,
              "max_score": 1, "breakdown": {}, "evidence_complete": False,
              "answer_isolation": "Prompt only; same-host filesystem access is not a security boundary"}
    team, adapter, sid, gid = None, None, None, None
    start_attempted = False
    try:
        team = Singlebox(config, source, directory)
        info["profile_sha256"] = team.digest
        info["member_profiles"] = team.mapping
        write(directory / "run.json", info)
        start_attempted = True
        progress("启动本次五个评测Bot；详细日志: singlebox-start.log")
        team.command("start")
        progress("五个Bot已启动；创建群并一次性提交协作目标")
        members = team.members()
        progress(f"已确认成员: {len(members)}；执行超时上限: {case['meta']['timeout_seconds']}秒")
        info["members"] = members
        adapter = BcnAdapter(config["repository"] / "apps/bcs/target/debug/bcs-cli", config["bcs_url"], team.bcn_env())
        info["phase"] = "submitting"
        write(directory / "run.json", info)
        # There is exactly one mutating dispatch. Initial group context triggers Manager.
        started = time.monotonic()
        gid, sid = adapter.create(members, case["sections"]["Prompt"].strip(), directory.name)
        info.update(group_id=gid, session_id=sid, phase="executing")
        write(directory / "run.json", info)
        progress(f"Group: {gid}; Session: {sid}")
        last_notice = 0
        def record_state(state):
            nonlocal last_notice
            write(evidence / "session.json", state)
            now = time.monotonic()
            if now - last_notice >= 30 or state.get("status") != "running":
                progress(f"BCN状态: {state.get('status')}; 已执行 {int(now - started)}s / {case['meta']['timeout_seconds']}s")
                last_notice = now
        status, final = adapter.wait(sid, started + case["meta"]["timeout_seconds"], record_state)
        progress(f"执行结果: {status}")
        result["execution_status"] = status
        result["execution_seconds"] = time.monotonic() - started
        if final:
            (artifacts / "final.md").write_text(final, encoding="utf-8")
    except TimeoutError:
        result["execution_status"] = "timeout"
        progress("执行超时；开始收集证据和清理，不判为成功")
    except KeyboardInterrupt:
        result["execution_status"] = "cancelled"
        progress("收到取消请求；开始清理本次资源")
    except Exception as error:
        # Do not expose API errors or credentials. Raw service diagnostics remain local.
        if sid:
            result["execution_status"] = "failed"
        progress(f"执行异常: {type(error).__name__}；详见本地日志")
        result["error_type"] = type(error).__name__
        result["error"] = str(error) if isinstance(error, (ValueError, RuntimeError)) else "Runtime failed; inspect private local logs"
    finally:
        # Stop owned bots before reading logs so files are stable; never stop shared services.
        if team and start_attempted:
            try:
                progress("停止本次评测Bot；详细日志: singlebox-stop.log")
                team.command("stop")
                team.verify_stopped()
                result["bots_stopped"] = True
                progress("本次Bot已停止，端口退出检查通过")
            except Exception:
                result["bots_stopped"] = False
                progress("警告：Bot清理未确认成功，请检查singlebox-stop.log")
                result["cleanup_warning"] = "Inspect owned profile/ports in run.json; shared services were not stopped"
        if adapter and gid:
            try:
                adapter.call("terminate-group", "--group", gid, as_json=False)
                result["group_terminated"] = True
                progress("本次评测群已终止")
            except Exception:
                result["group_terminated"] = False
                progress("警告：本次群终止失败，请核查群状态")
        if adapter and sid:
            progress("回收群消息、各成员对话和模型用量")
            try:
                messages = adapter.call("session", "messages", sid, "--limit", "1000")
                write(evidence / "group-messages.json", messages)
                # A capped response is explicitly incomplete; no silent completeness claim.
                result["group_messages_complete"] = isinstance(messages, list) and len(messages) < 1000
            except Exception:
                result["group_messages_complete"] = False
            per_member = {}
            for alias, profile in team.mapping.items():
                try:
                    per_member[alias] = collect_member(team.root / "profiles" / profile, sid, evidence / alias)
                except Exception:
                    per_member[alias] = {"evidence_complete": False, "total_tokens": None, "missing": ["collection failed"]}
            for alias, item in per_member.items():
                progress(f"成员 {alias}: 日志完整={item['evidence_complete']}, 模型输出={item.get('assistant_messages', '未知')}, Token={item['total_tokens'] if item['total_tokens'] is not None else '未知'}")
            result["member_evidence"] = per_member
            result["evidence_complete"] = result.get("group_messages_complete", False) and all(v["evidence_complete"] for v in per_member.values())
            totals = [v["total_tokens"] for v in per_member.values()]
            result["total_tokens"] = sum(totals) if all(v is not None for v in totals) else None
            calls = [v.get("tool_calls") for v in per_member.values()]
            result["tool_calls"] = sum(calls) if all(v is not None for v in calls) else None
            result["member_activity"] = {k: ("assistant_messages_observed" if v.get("assistant_messages", 0) else "not_verified") for k, v in per_member.items()}
        if team and not result.get("bots_stopped"):
            result["grading_status"] = "skipped"
        elif result["execution_status"] == "completed":
            try:
                progress("执行代码评分与模型评分")
                auto = code_grade(case, artifacts, config["judge"]["timeout_seconds"])
                result["breakdown"]["automated"] = auto
                # Send only business evidence, never local paths, profiles or runtime secrets.
                messages_path = evidence / "group-messages.json"
                messages = json.loads(messages_path.read_text()) if messages_path.is_file() else []
                judged = llm_grade(case, (artifacts / "final.md").read_text(), messages,
                                   config["judge"], os.environ[config["judge"]["api_key_env"]])
                result["breakdown"]["llm_judge"] = judged["scores"]
                result["notes"] = judged["notes"]
                result["judge_usage"] = judged["usage"]
                result["score"] = combine(auto, judged["scores"], case["meta"]["grading_weights"])
                result["grading_status"] = "completed"
            except Exception as error:
                result["grading_status"] = "error"
                result["grading_error_type"] = type(error).__name__
                progress(f"评分失败: {type(error).__name__}；得分保留null")
        if result["grading_status"] == "skipped":
            progress("未满足正式收卷/清理条件，跳过质量评分；仍生成分析报告")
        info.update(finished_at=time.time(), phase="finished")
        write(directory / "run.json", info)
        write(directory / "result.json", result)
        (directory / "report.md").write_text("# BCN Bench result\n\n```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n\nEvidence: evidence/; final delivery: artifacts/final.md.\nPrivate runtime files/logs must not be uploaded.\n")
    try:
        from report import generate_report
        progress(f"根据归档证据生成分析报告（接口超时上限{config['judge']['timeout_seconds']}秒）")
        report_path = generate_report(directory, config)
        progress(f"分析报告: {report_path}")
    except Exception as error:
        progress(f"Analysis report unavailable ({type(error).__name__}); raw result and evidence retained")
    progress(f"Finished: execution={result['execution_status']}, grading={result['grading_status']}")
    progress(f"得分: {result['score'] if result['score'] is not None else '未评分'}；结果: {directory / 'result.json'}；运行日志: {directory / 'runner.log'}")
    return directory, result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--runtime", help="Optional override; defaults reuse prepared Singlebox model configuration")
    parser.add_argument("--check", action="store_true", help="Validate inputs only; no services/model calls")
    args = parser.parse_args()
    emit_progress("校验Case、Profile和本机运行配置（不启动共享服务）")
    case = load_case(args.case)
    config = load_runtime(args.runtime)
    source = Path(args.profile_dir).resolve()
    validate_profile(source)
    if args.check:
        print("Inputs valid; no runtime started. Live API and isolation behavior not yet verified.")
        return 0
    def cancelled(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    path, result = run(case, config, source)
    print(f"Report: {path / 'report.md'}")
    return 0 if result["execution_status"] == "completed" and result["grading_status"] == "completed" and result.get("bots_stopped") else 1

if __name__ == "__main__":
    raise SystemExit(main())
