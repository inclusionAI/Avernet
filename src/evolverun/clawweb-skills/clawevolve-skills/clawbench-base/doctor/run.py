#!/usr/bin/env python3
"""
PinchBench Doctor - Entry Point

Run diagnosis on benchmark results to identify issues and generate optimization suggestions.

Usage:
    python run.py --run-id 0042 --model kimi-k2-5
    python run.py --run-id 0042 --model kimi-k2-5 --verbose
    python run.py --run-id 0042 --model kimi-k2-5 --output-dir ./my_diagnosis
    python run.py --list-runs

Examples:
    # List available runs
    python run.py --list-runs

    # Diagnose a specific run
    python run.py --run-id 0042 --model antchat-kimi-k2-5

    # With verbose output
    python run.py --run-id 0042 --model antchat-kimi-k2-5 -v

    # Custom output directory
    python run.py --run-id 0042 --model antchat-kimi-k2-5 --output-dir ./diagnosis_results
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

# Ensure the parent directory is in the path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

from lib_doctor import DoctorEngine


def list_runs(results_dir: Path) -> None:
    """List available run IDs and their transcripts."""
    print("\n📁 Available runs in:", results_dir)
    print("-" * 50)

    if not results_dir.exists():
        print("  ❌ Results directory not found")
        return

    # Find all result files
    run_ids = set()
    for f in results_dir.glob("*_benchmark_report.json"):
        # Extract run_id from filename like "20250415-143022-a1b2c3d4_glm-5_benchmark_report.json"
        match = re.match(r"(\d{8}-\d{6}-[a-f0-9]{8})_", f.stem)
        if match:
            run_ids.add(match.group(1))

    if not run_ids:
        print("  No runs found")
        return

    for run_id in sorted(run_ids):
        # Find corresponding transcripts (new format: run_id_model_slug_transcripts)
        # Look for any directory matching {run_id}_*_transcripts
        model_names = set()
        transcript_count = 0

        for transcripts_dir in results_dir.glob(f"{run_id}_*_transcripts"):
            if transcripts_dir.is_dir():
                files = list(transcripts_dir.glob("*.jsonl"))
                transcript_count = len(files)
                # Extract model name from directory name
                parts = transcripts_dir.stem.split("_", 1)
                if len(parts) > 1:
                    model_names.add(parts[1].replace("_transcripts", ""))

        models_str = ", ".join(sorted(model_names)) if model_names else "unknown"
        print(f"  Run {run_id}: {transcript_count} transcripts, models: {models_str}")

    print("-" * 50)
    print(f"Total: {len(run_ids)} runs\n")


def main():
    parser = argparse.ArgumentParser(
        description="PinchBench Doctor - Session Diagnosis System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available runs
  python run.py --list-runs

  # Diagnose a specific run
  python run.py --run-id 0042 --model antchat-kimi-k2-5

  # With LLM deep analysis (requires ANTHROPIC_API_KEY)
  python run.py --run-id 0042 --model antchat-kimi-k2-5 --enable-llm

  # With verbose output
  python run.py --run-id 0042 --model antchat-kimi-k2-5 -v

  # Custom output directory
  python run.py --run-id 0042 --model antchat-kimi-k2-5 --output-dir ./diagnosis_results
        """,
    )

    parser.add_argument(
        "--run-id",
        required=False,
        help="Benchmark run ID (e.g., 0042)",
    )
    parser.add_argument(
        "--model",
        required=False,
        help="Model name (e.g., antchat-kimi-k2-5, kimi-k2-5)",
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help="Benchmark name for context (e.g., yuque_bench, pinchbench)",
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Directory containing benchmark results (default: ../results)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for diagnosis reports (default: ./output)",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.6,
        help="Score threshold for diagnosis - tasks below this will be analyzed (default: 0.6)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--list-runs",
        action="store_true",
        help="List available run IDs and exit",
    )
    parser.add_argument(
        "--enable-llm",
        action="store_true",
        help="Enable LLM-based deep analysis (requires ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="LLM model to use for analysis (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--scene",
        default="default",
        help='Scene name to lookup benchmark results (default: "default")',
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    # Determine directories
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # Scene-based directory structure
    if args.results_dir:
        results_dir = Path(args.results_dir)
    else:
        results_dir = project_root / "results" / "benchmark" / args.scene

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = project_root / "results" / "doctor" / args.scene

    # Handle --list-runs
    if args.list_runs:
        list_runs(results_dir)
        return

    # Validate required arguments for diagnosis
    if not args.run_id or not args.model:
        parser.error("--run-id and --model are required for diagnosis (unless using --list-runs)")

    # Check results directory exists
    if not results_dir.exists():
        logger.error("Results directory not found: %s", results_dir)
        print(f"\n❌ Results directory not found: {results_dir}")
        print("   Please make sure the benchmark has been run first.")
        print(f"   Or specify a different directory with --results-dir")
        print(f"\n💡 Use --list-runs to see available runs")
        sys.exit(1)

    # Print banner
    print("\n" + "=" * 60)
    print("🏥 PinchBench Doctor - Session Diagnosis System")
    print("=" * 60)
    print(f"Run ID: {args.run_id}")
    print(f"Model: {args.model}")
    print(f"Results Dir: {results_dir}")
    print(f"Output Dir: {output_dir}")
    print(f"Score Threshold: {args.score_threshold}")
    print("=" * 60 + "\n")

    # Initialize and run diagnosis
    engine = DoctorEngine(
        results_dir=results_dir,
        output_dir=output_dir,
        score_threshold=args.score_threshold,
        enable_llm=args.enable_llm,
        llm_model=args.llm_model,
    )

    try:
        report = engine.diagnose(
            run_id=args.run_id,
            model=args.model,
            benchmark=args.benchmark,
        )

        output_path = engine.save_report(report)

        # Print summary
        print("\n" + "=" * 60)
        print("🏥 诊断完成")
        print("=" * 60)
        print(f"诊断任务数: {report.tasks_diagnosed}")
        print(f"发现问题数: {report.total_issues}")
        print(f"生成补丁数: {len(report.patches)}")

        # Count LLM insights
        total_llm_insights = sum(len(td.llm_insights) for td in report.task_diagnoses)
        if total_llm_insights > 0:
            print(f"🤖 LLM 深度洞察: {total_llm_insights}")

        if report.issues_by_severity:
            print("\n问题严重度分布:")
            for sev in ["high", "medium", "low"]:
                count = report.issues_by_severity.get(sev, 0)
                if count > 0:
                    emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
                    print(f"  {emoji} {sev.upper()}: {count}")

        if report.issues_by_pattern:
            print("\n问题类型分布:")
            pattern_names = {
                "P001": "工具调用反复失败",
                "P002": "参数上下文丢失",
                "P004": "必要工具未调用",
            }
            for pattern_id, count in sorted(report.issues_by_pattern.items()):
                name = pattern_names.get(pattern_id, "未知")
                print(f"  - {pattern_id} ({name}): {count}")

        print(f"\n📄 输出目录: {output_path}")
        print(f"   - diagnosis.json      完整诊断结果")
        print(f"   - diagnosis_summary.md  可读摘要")
        print(f"   - patches/            优化补丁")
        print("=" * 60 + "\n")

        sys.exit(0)

    except FileNotFoundError as e:
        logger.error("File not found: %s", e)
        print(f"\n❌ 错误: {e}")
        print("\n可能的原因:")
        print("  1. Run ID 不存在")
        print("  2. Model 名称不匹配")
        print("  3. 结果文件或 transcript 目录缺失")
        print(f"\n💡 使用 --list-runs 查看可用的运行 ID")
        sys.exit(1)

    except Exception as e:
        logger.exception("Diagnosis failed")
        print(f"\n❌ 诊断失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()