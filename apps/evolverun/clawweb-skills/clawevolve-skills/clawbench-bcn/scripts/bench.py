"""BCN Bench 公共入口：run 发起评测，finalize 补全独立评测报告。"""
import argparse
import os
import signal
from core.profile_loader import load_bench
from core.runner import run
from core.evaluation import finalize
from adapters.local.config import load_local_config
from adapters.local.runtime import LocalRuntime
from outputs.file import FileOutput, load_evaluation, save_evaluation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    launch = commands.add_parser('run', help='运行一套 Case/Profile')
    launch.add_argument('--mode', choices=['local'], required=True, help='当前仅实现 local；不支持的模式直接拒绝')
    launch.add_argument('--bench-dir', required=True)
    launch.add_argument('--model-config')
    launch.add_argument('--output-dir')
    launch.add_argument('--check', action='store_true', help='仅检查 Case/Profile，不检查服务或启动进程')
    finish = commands.add_parser('finalize', help='保存独立评测宿主的报告与评分')
    finish.add_argument('--run-dir', required=True)
    finish.add_argument('--report', required=True)
    finish.add_argument('--assessment', help='JSON: scores/total（llm_judge 或 hybrid）, notes, usage, evidence_notes')
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.command == 'finalize':
        request, result, report, assessment = load_evaluation(args.run_dir, args.report, args.assessment)
        completed = finalize(request, result, report, assessment)
        save_evaluation(args.run_dir, completed, report)
        print('评测报告已保存；执行状态保持不变', flush=True)
        return 0
    case, source, manifest = load_bench(args.bench_dir)
    if args.check:
        print(f"Bench valid: {case['meta']['id']}; members={len(manifest['bots'])}; no processes started")
        return 0
    config = load_local_config(args.model_config, args.output_dir)
    config['entry_member'] = manifest['entry_member']
    output = FileOutput(config['output_dir'])
    output.progress(f'运行目录: {output.directory}')
    runtime = LocalRuntime(config, source, output)
    def cancelled(*_):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        result = run(case, runtime, output, config['judge']['timeout_seconds'])
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(f'Evaluation handoff: {output.directory / "evaluation-request.json"}', flush=True)
    if not result.get('cleanup', {}).get('resources_released') or result['execution_status'] == 'environment_error':
        return 2
    if result['execution_status'] != 'completed':
        return 1
    return 3 if result['report_status'] == 'awaiting_evaluation_agent' else 0


if __name__ == '__main__':
    raise SystemExit(main())
