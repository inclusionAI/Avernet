#!/usr/bin/env python3
"""
Demo script for LLM-enhanced Doctor analysis.

This demonstrates how the LLM analyzer works with thinking content.
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from lib_doctor import TranscriptParser, DoctorEngine
from lib_llm_analyzer import LLMAnalyzer


def demo_thinking_extraction():
    """Demonstrate extraction of thinking content from transcripts."""
    print("=" * 70)
    print("🧠 Demo 1: Extracting Thinking Content from Transcript")
    print("=" * 70)

    # Find a sample transcript
    results_dir = Path(__file__).parent.parent / "results"
    transcripts_dirs = list(results_dir.glob("*_transcripts"))

    if not transcripts_dirs:
        print("❌ No transcripts found. Please run benchmark first.")
        return

    # Parse first transcript
    transcript_dir = transcripts_dirs[0]
    transcript_files = list(transcript_dir.glob("*.jsonl"))

    if not transcript_files:
        print("❌ No transcript files found.")
        return

    parser = TranscriptParser()
    events = parser.parse(transcript_files[0])

    # Extract thinking
    thinking_count = 0
    for event in events:
        if event.thinking:
            thinking_count += 1
            if thinking_count <= 2:  # Show first 2 thinking entries
                print(f"\n📝 Thinking #{thinking_count}:")
                print(f"   Timestamp: {event.timestamp}")
                print(f"   Content: {event.thinking[:300]}...")

    # Extract user query
    engine = DoctorEngine(
        results_dir=results_dir,
        output_dir=Path("/tmp"),
    )
    user_query = engine._extract_user_query(events)
    print(f"\n📋 User Query: {user_query[:200]}...")

    print(f"\n✅ Found {thinking_count} thinking entries in transcript")


def demo_llm_analyzer():
    """Demonstrate LLM analyzer initialization."""
    print("\n" + "=" * 70)
    print("🤖 Demo 2: LLM Analyzer Initialization")
    print("=" * 70)

    # Initialize without API key (dry-run mode)
    analyzer = LLMAnalyzer()
    print(f"\n📊 Dry-run mode (no API key):")
    print(f"   Available: {analyzer.is_available()}")

    # Check for API key
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        print(f"\n✅ API key found in environment")
        analyzer = LLMAnalyzer(api_key=api_key)
        print(f"   Available: {analyzer.is_available()}")
        print(f"   Model: {analyzer.model}")
    else:
        print(f"\n⚠️  No ANTHROPIC_API_KEY found in environment")
        print(f"   To enable LLM analysis, set:")
        print(f"   export ANTHROPIC_API_KEY='your-api-key'")


def demo_analysis_flow():
    """Demonstrate complete analysis flow."""
    print("\n" + "=" * 70)
    print("🔍 Demo 3: Complete Analysis Flow")
    print("=" * 70)

    import os
    results_dir = Path(__file__).parent.parent / "results"

    # Check if we can run full analysis
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if not has_api_key:
        print("\n⚠️  Running without LLM analysis (no API key)")
        engine = DoctorEngine(
            results_dir=results_dir,
            output_dir=Path("/tmp/doctor_demo"),
            enable_llm=False,
        )
    else:
        print("\n✅ Running with LLM analysis enabled")
        engine = DoctorEngine(
            results_dir=results_dir,
            output_dir=Path("/tmp/doctor_demo"),
            enable_llm=True,
        )

    # Find available runs
    run_dirs = list(results_dir.glob("*_transcripts"))
    if run_dirs:
        run_id = run_dirs[0].name.split("_")[0]
        print(f"\n📁 Analyzing run: {run_id}")

        try:
            report = engine.diagnose(
                run_id=run_id,
                model="antchat-kimi-k2-5",
            )

            print(f"\n📊 Results:")
            print(f"   Tasks diagnosed: {report.tasks_diagnosed}")
            print(f"   Issues found: {report.total_issues}")
            print(f"   Patches generated: {len(report.patches)}")

            # Count LLM insights
            total_insights = sum(len(td.llm_insights) for td in report.task_diagnoses)
            print(f"   LLM insights: {total_insights}")

            # Show sample insights
            if total_insights > 0:
                print(f"\n🧠 Sample LLM Insights:")
                for task in report.task_diagnoses:
                    for insight in task.llm_insights[:2]:
                        print(f"   - [{insight.category}] {insight.description[:80]}...")

        except Exception as e:
            print(f"\n❌ Analysis failed: {e}")


def print_usage_guide():
    """Print usage guide for LLM-enhanced diagnosis."""
    print("\n" + "=" * 70)
    print("📖 Usage Guide: LLM-Enhanced Diagnosis")
    print("=" * 70)

    print("""
1. Set up API key:
   export ANTHROPIC_API_KEY='your-api-key'

2. Run diagnosis with LLM analysis:
   cd doctor
   python run.py --run-id 0040 --model antchat-kimi-k2-5 --enable-llm

3. Specify custom LLM model:
   python run.py --run-id 0040 --model antchat-kimi-k2-5 \\
       --enable-llm --llm-model claude-sonnet-4-6

4. View results:
   - diagnosis_summary.md  # Human-readable report with 🤖 LLM insights
   - patches/memory/       # LLM-generated memory patches
   - patches/skill/        # LLM-generated skill patches

5. Key features:
   - 🧠 Cognitive gap analysis: Detects mismatches between intent and action
   - 📋 Entity recognition: Checks if agent understands user entities
   - 🔍 Root cause analysis: Deep analysis of why errors occur
   - 🎯 Targeted suggestions: Specific fixes based on thinking content
""")


if __name__ == "__main__":
    print("\n🏥 PinchBench Doctor - LLM Enhanced Analysis Demo\n")

    demo_thinking_extraction()
    demo_llm_analyzer()
    demo_analysis_flow()
    print_usage_guide()

    print("\n✨ Demo complete!")
