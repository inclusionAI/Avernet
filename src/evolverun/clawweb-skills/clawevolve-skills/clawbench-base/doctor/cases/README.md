# PinchBench Doctor - 案例库

本目录包含 Doctor 系统的各种诊断案例，用于展示不同问题模式的检测和优化。

## 可用案例

### P001 - 工具调用反复失败

**文件**: `P001_repeated_tool_call_failure.md`

**场景**: Agent 在调用 `skylark_doc_detail` 时，连续 2 次传入 `doc_id=null`，第 3 次才正确传入 `doc_id=12345`。

**运行方式**:
```bash
cd doctor
python run.py --run-id case_p001_demo --model antchat-kimi-k2-5
```

**预期输出**:
- 🔴 **P001**: 检测到工具调用反复失败 (HIGH)
- 🟡 **P004**: 检测到可能缺少必要工具 (MEDIUM)
- 生成 3 个优化补丁

**学习要点**:
1. 如何检测连续失败的工具调用
2. 如何分析失败原因（参数缺失）
3. 如何生成 Skill 和 Memory 优化建议

---

## 如何运行案例

### 1. 列出可用案例

```bash
python run.py --list-runs | grep case_
```

### 2. 运行指定案例

```bash
python run.py --run-id case_p001_demo --model antchat-kimi-k2-5
```

### 3. 查看详细报告

```bash
cat output/antchat-kimi-k2-5/case_p001_demo/diagnosis_summary.md
```

### 4. 查看生成的补丁

```bash
ls output/antchat-kimi-k2-5/case_p001_demo/patches/
```

---

## 创建新案例

如果你想创建一个新的测试案例，请参考 `P001_repeated_tool_call_failure.md` 中的"测试数据准备"部分。

基本步骤:
1. 创建 transcript JSONL 文件
2. 创建结果 JSON 文件
3. 运行诊断
4. 验证输出

---

## 案例数据结构

每个案例包含:

```
results/
├── case_{name}_transcripts/
│   └── task_{xxx}.jsonl       # Transcript 文件
└── case_{name}_{model}.json   # 结果汇总文件

doctor/output/{model}/case_{name}/
├── diagnosis.json              # 诊断结果
├── diagnosis_summary.md        # 可读报告
└── patches/
    ├── skill/                  # Skill 补丁
    └── memory/                 # Memory 补丁
```

---

*更多案例持续添加中...*