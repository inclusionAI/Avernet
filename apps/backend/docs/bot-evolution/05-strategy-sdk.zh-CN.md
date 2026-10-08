# 进化策略 SDK——让进化可插拔

> English version: [05-strategy-sdk.md](05-strategy-sdk.md)

> 状态：DRAFT（讨论稿）。说明其他团队如何在不修改平台代码的前提下，将一种新
> 的进化方法接入平台。

## 1. 原则

1. **平台拥有循环骨架与保留权；进化策略（Strategy）拥有变异，并参与选择。**
   进化策略可以决定*尝试什么*以及*什么算更好*；但它永远不能独自决定*什么上线*
   （见 [08-governance.zh-CN.md](08-governance.zh-CN.md)）。
2. **小粒度插件（plugin）种类，由清单组合。** 进化策略不是一个庞大的
   「Strategy」接口，而是若干窄插件的声明式组合。团队可以替换其中一块（例如更
   好的 Evaluator），并复用其余部分（例如默认的 Analyzer）。
3. **默认与语言无关。** 重型插件通过线协议在进程外运行，因此现有的 Python
   skill、TS 控制面、其他团队的容器或一个 bot 都可以实现它们。
4. **契约配套一致性测试。** 每种插件都有 JSON Schema 输入/输出契约和一致性
   测试套件（conformance kit，R25）。该套件随 SDK 一同发布，作者可在本地运行。
5. **有两个例子后再抽象（R19）。** 下列每种插件都至少有两个现有或调研过的实现
   作为支撑（见 §3 的表格）。

## 2. 插件种类

| 种类 | 输入 | 输出 | 是否必需？ |
| --- | --- | --- | --- |
| **ExperienceSource** | 引擎 + bot + 时间窗口 | 归一化的 `Episode[]` | 按引擎提供（平台级，不按进化策略） |
| **Trigger** | 信号（定时、指标、事件、收件箱） | `RunRequest?` | 可选（手动始终可用） |
| **Selector** | 归档视图 | 父修订版 | 可选（默认 `latest-active`） |
| **Analyzer** | 片段（episode）、反馈、收件箱条目 | `Findings`（兼容 `plan-source/v2`） | 可选 |
| **SuiteBuilder** | 发现项（findings） | 候选评测用例（由平台分配划分） | 可选 |
| **Proposer** | 父基因组（只读沙箱）、发现项、训练集失败用例及评语、历史 | `GenomePatch[]` + 理由 | **必需** |
| **Evaluator**（Executor + Grader） | 基因组修订版、用例集合、预算 | 逐用例得分 + 评语 + 轨迹 | **由验证器拥有**：进化策略选择一个验证配置（verification profile），并可贡献训练集用例，但评分器与执行器由验证器注册并做版本管理，而非由进化策略管理（[03-verification.zh-CN.md §3](03-verification.zh-CN.md#3-验证模型)） |
| **AcceptancePolicy** | 父版本与候选的评测结果对比 | 接纳 / 拒绝 / 继续 + 原因 | 可选（默认：无回归 + 在验证集上有提升） |
| **Curator** | 完整基因组 + 使用统计 | `GenomePatch`（去重、退役、合并） | 可选；是按计划运行的一种 Proposer 特化 |
| **MetaProposer** | 实验记录 H（导出）、父改进机制 | 改进机制补丁 | 仅限第 3 层；永远不能以其自身所属族或验证器为目标（[04-recursion.zh-CN.md](04-recursion.zh-CN.md)） |

刻意**不**作为插件的部分：记录修订版、静态检查、门禁的平台底线、晋升、
rollout、apply。这些是平台代码（DR-2）。

## 3. 每种插件真实存在的证据（R19）

| 种类 | 仓库中已有 | 调研对象 |
| --- | --- | --- |
| ExperienceSource | ClawEvolve `acquisition/discovery.py`+`sessions.py`（OpenClaw JSONL）；`service_export`（`session-export/v1`） | LangSmith traces、OpenAI trace grading |
| Analyzer | `clawevolve-diagnose`；workflow-run `SingleRunAnalyzer`/`BatchRunAnalyzer`；ClawInsight 改进适配器 | ExpeL 洞察提取、Trace2Skill |
| SuiteBuilder | `clawevolve-plan`（ClawBench 模板） | SkillWeaver 课程、Voyager 课程 |
| Proposer | `clawevolve-tune`（+review）；TaskGuard 修复策略；suggestion_apply | GEPA 反思式变异、ACE curator、OPRO、Dreams、Hermes `skill_manage` |
| Evaluator | `clawbench-base/lib_grading.py` | DSPy metrics、Promptfoo、LangSmith evaluators |
| AcceptancePolicy | `action_accept`（test > baseline）、可由环境变量调节的门禁、`calibrate_evolution_gates.py` | GEPA Pareto、DGM 归档准入 |
| Selector | 隐式取最新 | DGM、HGM clade-metaproductivity、MAP-Elites |
| MetaProposer | 人工运行：`calibrate_evolution_gates.py`、变异算子库整理 | Meta-Harness、ADAS、Promptbreeder（变异提示词自身进化）、DGM 自我修改 |
| Trigger | 手动、失败运行观察器、Insight 监控 | Hermes curator 空闲触发、OpenClaw dreaming cron |

## 4. 策略清单

一个进化策略是在 C3 中注册的、带版本的 JSON 文档。与本设计中所有新增内容一样，
它在编写和存储时都是 JSON；只有现有的 Bot Config Manifest 保持 YAML
（[02-genome.zh-CN.md §7.3](02-genome.zh-CN.md#73-序列化规范-json)）。

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "strategy_schema": 1,
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "description": "Diagnose real sessions, tune persona and local skills, gate on ClawBench.",
  "applies_to": {
    "engines": ["openclaw", "claude_code"],       // checked against engine capabilities
    "genes": ["persona", "skills"]                // max scope this strategy may patch
  },
  "requires_capabilities": ["session_export.v1", "eval_env.sandbox"],
  "flow": [
    {"step": "analyze", "plugin": "clawevolve/diagnose@1.4",
     "params": {"window_days": 7, "max_cases": 40}},
    {"step": "build_suite", "plugin": "clawevolve/plan@1.2"},
    {"loop": {"max_iterations": 3, "until": "policy.stop"},
     "steps": [
       {"step": "propose", "plugin": "clawevolve/tune-review@2.0"},
       {"step": "evaluate", "plugin": "platform/clawbench@1",   // platform-provided
        "splits": ["train", "validation"]},
       {"step": "decide", "plugin": "clawevolve/acceptance@1"}  // "validation > parent, paired win-rate ≥ 0.6"
     ]}
  ],
  "defaults": {
    "selector": "platform/latest-active@1",
    "budget": {"max_usd": 20, "max_wall_clock_s": 7200, "max_rollouts": 400},
    "models": {"proposer": "${MODEL_STRONG}", "judge": "${MODEL_JUDGE}"}  // resolved by config, no hardcoded endpoints
  }
}
```

flow 词汇表刻意保持精简：`step`、`loop`、`parallel`（提议器种群）、`when`
（条件）。它泛化了 ClawEvolve 冻结的 `{key, version, stages}` 以及
`preprocess | postprocess | replace` 阶段扩展模型：一个扩展只不过是复用另一个
进化策略的插件并替换其中一步的进化策略。

## 5. 执行绑定

一个插件实现声明一种绑定。

| 绑定 | 适用场景 | 方式 |
| --- | --- | --- |
| **进程内（Python）** | 廉价、确定性的插件：Selector、AcceptancePolicy、Trigger | 实现 `plugin_api` Protocol 的 Python 包，通过 entry point 注册；由 `apps/evolution` 组合根按配置加载（R5/R14） |
| **作业 worker** | 重型或非 Python 插件：Analyzer、Proposer、Evaluator、SuiteBuilder | 进程外 worker 使用**作业协议（Job Protocol）**通信；打包为容器镜像或 skill bundle |
| **执行者 Bot** | 插件*本身*就是一个 agent（ClawEvolve tune 是一个 OpenClaw agent） | 一个装配了该插件 skill bundle + `avn` CLI 的 bot；它与 worker 完全一样地认领作业，以带 `evolution:runner` scope 的 bot 主体身份认证 |

### 作业协议（线协议）

泛化自 ClawEvolve 的 `/internal/tasks/:id/steps/:sid/{claim,input,output,report}`：

```text
POST /evolution/v1/jobs:claim            {worker_id, plugin_ids[], capabilities} → job | 204
GET  /evolution/v1/jobs/{id}/input       → typed input (JSON Schema per plugin kind) + artifact URLs
POST /evolution/v1/jobs/{id}/heartbeat   {progress, note}         (lease extension; fencing token)
PUT  /evolution/v1/jobs/{id}/artifacts/{name}                     (content-addressed upload)
POST /evolution/v1/jobs/{id}/complete    typed output             (validated against schema)
POST /evolution/v1/jobs/{id}/fail        {reason, retryable}
```

- 使用带 fencing token 的租约，使卡住的 worker 无法完成一个已被重新分配的作业。
- 输入由编排器按种类契约过滤（Proposer 的输入永远不包含封存集（holdout）/回归集
  用例）。
- worker **不获得任何访问目标 bot 的凭据**；它们拿到的是限定于该作业的沙箱句柄
  （评测 Bot 或一个物化的工作区）。

## 6. SDK 包

| 包 | 面向 | 内容 |
| --- | --- | --- |
| `avernet-evolution`（Python）、`@avernet/evolution`（TS） | 调用方（管线、CI、UI 后端） | 由 OpenAPI 生成的客户端 + 易用的辅助方法（`runs.start`、`genomes.diff`、`candidates.review`） |
| `avernet-evolution-plugin`（Python 优先，TS 其次） | 进化策略作者 | 每个契约的类型化模型；每种插件的基类；`JobWorker` 循环；`GenomeWorkspace` 辅助工具，可将修订版物化到临时目录，并将编辑 diff 回 `GenomePatch`；本地测试环境（`avn strategy dev`），带内存编排器和假引擎；**一致性测试套件**（pytest 插件） |

`GenomeWorkspace` 辅助工具是最重要的易用性组件：大多数现有提议器（ClawEvolve
tune、Meta-Harness/DGM 风格的编码 agent 提议器）都想*在一个文件夹里编辑文件*。
该辅助工具让它们针对沙箱副本这样做，并把结果转换为逐项补丁，从而作者无需手写补
丁操作。

```python
# 仅作示意
from avernet_evolution_plugin import Proposer, ProposeInput, GenomeWorkspace

class MyProposer(Proposer):
    def propose(self, inp: ProposeInput) -> list[GenomePatch]:
        with GenomeWorkspace.materialise(inp.parent) as ws:
            run_my_agent(ws.path, findings=inp.findings, failures=inp.train_failures)
            return [ws.to_patch(rationale="…", evidence=inp.findings.ids())]
```

## 7. 注册与信任

1. 作者通过 API/CLI 将插件实现（镜像 digest 或 skill bundle digest）+ 清单发布
   到 C3。
2. C3 在沙箱中运行一致性测试套件；只有状态为 `conformant` 的实现才能被非 dev
   进化策略引用。
3. 每个实现都要声明**隔离级别、允许与禁止的能力**（R13）：例如「网络：仅模型
   提供方」「除作业输入外不访问租户数据」。
4. 进化策略默认按租户隔离；`platform/*` 和 `clawevolve/*` 是平台发布的默认策略。
   将第三方进化策略提升为平台级是一项需要评审的操作。
5. bot 所有者（或租户管理员）按 bot **启用**进化策略，并设定限制（基因、预算、
   调度、自动晋升上限）。启用是一次策略（policy）变更，而非基因组变更。

## 8. 一致性测试形态

遵循 `docs/arch/protocol-contract-tests.md`：对每种插件，用本地实现驱动**消费方**
（编排器），断言可观察的结果，并断言插件确实被调用。另外为作者提供一个种类级
的测试套件：

- Proposer：输出通过校验；base 匹配；从不触碰锁定基因；声明为确定性时，给定
  seed 结果确定；遵守预算信号；声明为本地时可在无网络情况下运行。
- Evaluator：相同修订版 + 相同用例 + seed → 得分在容差范围内；为每个失败用例返
  回评语；从不修改修订版。
- AcceptancePolicy：输入的纯函数；全函数（总是返回一个决定）。
