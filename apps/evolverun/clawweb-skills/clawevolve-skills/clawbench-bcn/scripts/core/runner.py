"""Shared Bench lifecycle; runtime and output are injected by the CLI."""
import time
from .contracts import Runtime, Output
from .grading import code_grade
from .evaluation import evaluation_request
from .transcript import build_transcript


def run(case: dict, runtime: Runtime, output: Output, judge_timeout=120, poll_interval=2):
    def progress(message):
        # Logging failure must not prevent owned-resource cleanup.
        try:
            output.progress(message)
        except OSError:
            pass

    info = {'run_id': output.run_id, 'case_id': case['meta']['id'], 'strategy': 'manager_worker',
            'case_sha256': case.get('case_sha256'), 'started_at': time.time()}
    result = {'execution_status': 'environment_error', 'grading_status': 'skipped',
              'score': None, 'max_score': 1, 'breakdown': {}, 'evidence_complete': False,
              'evidence_stable': False, 'total_tokens': None, 'usage_complete': False}
    evidence = {'messages': [], 'session': {}}
    transcript = []
    submitted = False
    started = None
    try:
        progress('准备本次运行')
        runtime.prepare()
        info.update(runtime.metadata, phase='submitting')
        output.write_json('run.json', info)
        started = time.monotonic()
        info.update(runtime.submit(case['sections']['Prompt'].strip(), case['entry_member']))
        submitted = True
        info['phase'] = 'executing'
        output.write_json('run.json', info)
        progress(f"Group: {info.get('group_id')}; Session: {info.get('session_id')}")
        deadline = started + case['meta']['timeout_seconds']
        last_notice = None
        result['execution_status'] = 'timeout'
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                state = runtime.poll(max(.1, min(30, remaining)))
            except TimeoutError:
                if time.monotonic() >= deadline:
                    break
                raise RuntimeError('Status query timed out before task deadline') from None
            evidence['session'] = state.get('session', {})
            output.write_json('evidence/session.json', evidence['session'])
            status = state['status']
            now = time.monotonic()
            if last_notice is None or now - last_notice >= 30 or status != 'running':
                progress(f"BCN状态: {status}; 已执行 {int(now - started)}s / {case['meta']['timeout_seconds']}s")
                last_notice = now
            if status != 'running':
                if status not in ('completed', 'failed', 'cancelled', 'timeout'):
                    raise ValueError('Unsupported normalized execution status')
                final = state.get('output')
                if status == 'completed' and (not isinstance(final, str) or not final.strip()):
                    status = 'failed'
                result['execution_status'] = status
                if status == 'completed':
                    output.write_text('artifacts/final.md', final)
                break
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    except TimeoutError:
        result['execution_status'] = 'timeout' if submitted else 'environment_error'
    except KeyboardInterrupt:
        result['execution_status'] = 'cancelled'
    except Exception as error:
        result['execution_status'] = 'failed' if submitted else 'environment_error'
        result['error_type'] = type(error).__name__
        progress(f"执行异常: {type(error).__name__}；检查本次私有运行日志")
    finally:
        if started is not None:
            result['execution_seconds'] = time.monotonic() - started
        progress(f"执行结果: {result['execution_status']}")
        try:
            boundary = runtime.quiesce(result['execution_status'])
            result['evidence_stable'] = boundary.get('evidence_stable') is True
            result['cleanup'] = boundary
        except Exception as error:
            result['cleanup'] = {'quiesce_error': type(error).__name__}
            progress('警告：证据边界未确认，跳过质量评分')
        try:
            collected = runtime.collect()
            evidence.update({k: collected[k] for k in ('messages', 'session') if k in collected})
            # Evidence must not overwrite execution/grade facts.
            for key in ('evidence_complete', 'usage_complete', 'total_tokens', 'tool_calls',
                        'group_messages_complete', 'member_evidence', 'evidence_origin'):
                if key in collected.get('summary', {}):
                    result[key] = collected['summary'][key]
            output.write_json('evidence/session.json', evidence['session'])
            output.write_json('evidence/group-messages.json', evidence['messages'])
            transcript = collected.get('transcript')
            if transcript is None:
                transcript = build_transcript(evidence['messages'], info.get('session_id', ''))
            if not isinstance(transcript, list):
                raise ValueError('Judge transcript must be a message list')
            output.write_json('artifacts/transcript.json', transcript)
        except Exception as error:
            result['evidence_complete'] = False
            result['collection_error'] = type(error).__name__
        finally:
            try:
                result.setdefault('cleanup', {}).update(runtime.release())
            except Exception as error:
                result.setdefault('cleanup', {}).update(resources_released=False, release_error=type(error).__name__)
                progress('警告：自有资源清理未确认，请检查运行结果')
        info.update(runtime.metadata)
        if result['execution_status'] == 'completed' and result['evidence_stable'] and result['evidence_complete']:
            try:
                output.write_json('artifacts/bcn-evidence.json', {'run': info, 'result': result, **evidence})
                kind = case['meta']['grading_type']
                if kind in ('automated', 'hybrid'):
                    progress('执行代码评分')
                    auto = code_grade(case, output.artifacts, judge_timeout, transcript)
                    result['breakdown']['automated'] = auto
                if kind == 'automated':
                    result.update(score=sum(auto.values()) / len(auto), grading_status='completed')
                else:
                    result['grading_status'] = 'awaiting_evaluation_agent'
            except Exception as error:
                result.update(grading_status='error', grading_error_type=type(error).__name__)
        if result['grading_status'] == 'skipped':
            progress('未满足正式收卷/证据条件，未评分；仍交接分析报告')
        result['report_status'] = 'awaiting_evaluation_agent'
        info.update(finished_at=time.time(), phase='finished')
        output.save_result(info, result)
        output.write_json('evaluation-request.json', evaluation_request(case))
        progress(f"执行={result['execution_status']}；评分={result['grading_status']}；得分={result['score'] if result['score'] is not None else '未评分'}")
        progress('evaluation-request.json 已就绪；等待独立评测 Agent 生成报告')
    return result
