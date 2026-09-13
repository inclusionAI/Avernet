#!/usr/bin/env python3
"""
Demo: Integrating Doctor with AgentBench.

This demonstrates how the new Doctor integration works.
"""

import sys
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib_integration import DoctorIntegration, DoctorAnalyzer, TranscriptExtractor


def demo_extractor():
    """Demonstrate transcript extraction."""
    print("=" * 70)
    print("📝 Demo 1: Transcript Extraction")
    print("=" * 70)

    # Sample transcript with thinking and tool calls
    sample_transcript = [
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "[Wed 2026-04-08 GMT] Search for Python docs"}]
            }
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "I need to search for Python documentation."},
                    {"type": "toolCall", "name": "skylark_search", "id": "call_001", "arguments": {"q": "Python"}}
                ]
            }
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_001",
                "toolName": "skylark_search",
                "content": [{"type": "text", "text": '{"docs": [{"id": 123, "title": "Python Guide"}]}'}],
                "isError": False
            }
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Now I need to get the doc details."},
                    {"type": "toolCall", "name": "skylark_doc_detail", "id": "call_002", "arguments": {"doc_id": None}}
                ]
            }
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_002",
                "toolName": "skylark_doc_detail",
                "content": [{"type": "text", "text": "Error: doc_id is required"}],
                "isError": True
            }
        }
    ]

    extractor = TranscriptExtractor()

    user_query = extractor.extract_user_query(sample_transcript)
    thinking_count = extractor.extract_thinking_count(sample_transcript)
    tool_calls = extractor.extract_tool_calls(sample_transcript)

    print(f"\n✅ Extraction Results:")
    print(f"   User query: {user_query[:50]}...")
    print(f"   Thinking entries: {thinking_count}")
    print(f"   Tool calls: {len(tool_calls)}")

    for tc in tool_calls:
        status = "❌" if tc.get("is_error") else "✅"
        print(f"      {status} {tc['tool_name']}: {tc['arguments']}")


def demo_rule_diagnosis():
    """Demonstrate rule-based diagnosis."""
    print("\n" + "=" * 70)
    print("🔍 Demo 2: Rule-Based Diagnosis")
    print("=" * 70)

    sample_transcript = [
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "toolCall", "name": "skylark_doc_detail", "id": "call_001", "arguments": {"doc_id": None}}
                ]
            }
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_001",
                "toolName": "skylark_doc_detail",
                "content": [{"type": "text", "text": "Error: doc_id required"}],
                "isError": True
            }
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "toolCall", "name": "skylark_doc_detail", "id": "call_002", "arguments": {"doc_id": None}}
                ]
            }
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_002",
                "toolName": "skylark_doc_detail",
                "content": [{"type": "text", "text": "Error: doc_id required"}],
                "isError": True
            }
        }
    ]

    analyzer = DoctorAnalyzer(enable_llm=False)
    diagnosis = analyzer.diagnose("task_demo", sample_transcript, score=0.5)

    if diagnosis:
        print(f"\n✅ Diagnosis found!")
        print(f"   Task: {diagnosis.task_id}")
        print(f"   Issues: {len(diagnosis.issues)}")
        for issue in diagnosis.issues:
            emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(issue.severity.value, "⚪")
            print(f"      {emoji} [{issue.pattern_id}] {issue.description[:70]}...")
    else:
        print("\nℹ️ No issues detected")


def demo_full_integration():
    """Demonstrate full integration flow."""
    print("\n" + "=" * 70)
    print("🤖 Demo 3: Full Integration")
    print("=" * 70)

    import os
    results_dir = Path(__file__).parent.parent / "results"

    if not results_dir.exists():
        print("\n❌ Results directory not found. Please run AgentBench first.")
        return

    # Initialize integration
    integration = DoctorIntegration(
        output_dir=results_dir,
        enable_llm=False,
        score_threshold=1.0,
    )

    # Find available runs
    run_dirs = list(results_dir.glob("*_transcripts"))
    if not run_dirs:
        print("\n❌ No transcript directories found.")
        return

    run_id = run_dirs[0].name.split("_")[0]
    transcript_dir = run_dirs[0]
    transcript_files = list(transcript_dir.glob("*.jsonl"))

    print(f"\n📁 Analyzing run: {run_id}")
    print(f"   Found {len(transcript_files)} transcripts")

    # Process each transcript
    for tf in transcript_files[:2]:
        task_id = tf.stem

        # Load transcript
        import json
        transcript = []
        for line in tf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    transcript.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        # Diagnose
        diagnosis = integration.diagnose_task(
            task_id=task_id,
            transcript=transcript,
            score=0.5,
        )

        if diagnosis:
            print(f"   {task_id}: {len(diagnosis.issues)} issues")

    # Save report
    report_path = integration.save_report(run_id, "demo-model")
    if report_path:
        print(f"\n📄 Report saved: {report_path}")

    # Print summary
    integration.print_summary()


def print_integration_guide():
    """Print integration guide."""
    print("\n" + "=" * 70)
    print("📖 Integration Guide")
    print("=" * 70)

    print("""
## Option 1: Modify benchmark.py (Recommended)

See integration_patch.py for the complete patch.

Key changes (only ~50 lines):
1. Add import
2. Add CLI arguments
3. Initialize DoctorIntegration
4. Call diagnose_task() after grading
5. Save report at end

## Option 2: Standalone Mode

After benchmark completes:

```bash
python doctor/run.py --run-id 0042 --model antchat-kimi-k2-5
```

## Why This Design?

1. Rule-based first: Zero cost for normal tasks
2. LLM optional: Only pay for insights when needed
3. Minimal changes: ~50 lines in benchmark.py
4. Error isolation: Try-except protects benchmark
""")


if __name__ == "__main__":
    print("\n🏥 Doctor Integration Demo\n")

    demo_extractor()
    demo_rule_diagnosis()
    demo_full_integration()
    print_integration_guide()

    print("\n✨ Demo complete!")
