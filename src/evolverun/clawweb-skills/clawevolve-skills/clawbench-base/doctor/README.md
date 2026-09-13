# 🏥 PinchBench Doctor

Session Diagnosis System for PinchBench - Analyzes agent session transcripts to identify issues and generate optimization suggestions.

## Overview

PinchBench Doctor 是一个诊断工具，用于分析 OpenClaw Agent 在基准测试中的行为。它采用**规则+LLM 混合架构**：

- **规则引擎**：快速检测常见问题（零成本、毫秒级）
- **LLM 分析**：深度理解认知偏差（按需启用、秒级）

## Features

### Rule-Based Detection (Foundation)

Always active, zero cost:

- **P001**: 工具调用反复失败 - 连续多次调用同一工具且失败
- **P002**: 参数上下文丢失 - 应该从前面工具结果获取的参数为空
- **P004**: 必要工具未调用 - 任务需要但没有调用的工具

### LLM Deep Analysis (Optional)

Triggered only when rule-based issues are found:

- **🧠 Cognitive Gap**: 对比 Agent thinking 与实际 tool call
- **📋 Entity Recognition**: 检测 Agent 是否正确理解用户指令
- **🔍 Root Cause**: 深入分析 why 错误发生

## Quick Start

### Standalone Mode (Post-Analysis)

```bash
cd doctor

# Basic diagnosis (rule-based only)
python run.py --run-id 0042 --model antchat-kimi-k2-5

# With LLM analysis
export ANTHROPIC_API_KEY="your-api-key"
python run.py --run-id 0042 --model antchat-kimi-k2-5 --enable-llm
```

### Integrated Mode (Real-Time)

Add to `scripts/benchmark.py`:

```python
# 1. Import (add at top)
from doctor.lib_integration import DoctorIntegration

# 2. Initialize (after parsing args)
doctor = DoctorIntegration(
    output_dir=Path(args.output_dir),
    enable_llm=args.enable_llm_diagnosis,
)

# 3. Diagnose (in task loop, after grading)
diagnosis = doctor.diagnose_task(
    task_id=task.task_id,
    transcript=result["transcript"],
    score=grade.score,
)

# 4. Report (at end)
doctor.save_report(run_id, args.model)
```

See [integration_patch.py](integration_patch.py) for complete patch.

## Architecture

```
Doctor
├── lib_integration.py   # Main integration API
├── lib_patterns.py      # Rule-based detection (P001/P002/P004)
├── lib_llm_analyzer.py  # Optional LLM analysis
├── lib_optimizer.py     # Patch generation
└── lib_report.py        # Report generation

Usage Flow:
1. Extract: Parse transcript (thinking, tool calls, query)
2. Rule Detection: Always run (fast, zero cost)
3. LLM Analysis: Optional (only if issues found)
4. Generate: Patches and reports
```

## Output

```
results/
├── 0042_antchat-kimi-k2-5.json          # Benchmark results
├── 0042_transcripts/                    # Session transcripts
└── diagnosis/                           # Doctor output
    ├── 0042_antchat-kimi-k2-5_summary.json
    └── 0042_antchat-kimi-k2-5/
        ├── task_01_diagnosis.json
        └── ...
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--enable-diagnosis` | false | Enable diagnosis |
| `--enable-llm-diagnosis` | false | Enable LLM analysis |
| `--diagnosis-score-threshold` | 1.0 | Only diagnose tasks below this score |

## Why Rule + LLM?

| Metric | Rule Only | LLM Only | Hybrid |
|--------|-----------|----------|--------|
| Speed | ⚡ ms | 🐢 s | ⚡ ms (avg) |
| Cost | $0 | $$$ | $$ (saves 80%) |
| Accuracy | Good | Better | Best |
| Reliability | 100% | 99% | 100% |

**Hybrid approach**: Run rules first (filter), then LLM only on problematic tasks.

## Documentation

- [AGENTBENCH_INTEGRATION_SPEC.md](docs/AGENTBENCH_INTEGRATION_SPEC.md) - Integration specification
- [IMPACT_ASSESSMENT.md](docs/IMPACT_ASSESSMENT.md) - Impact on existing code
- [INTEGRATION_DESIGN.md](docs/INTEGRATION_DESIGN.md) - Design draft

## License

Same as PinchBench project.
