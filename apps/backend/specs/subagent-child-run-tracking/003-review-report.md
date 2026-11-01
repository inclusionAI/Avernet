---
agent: tc-code-reviewer
status: completed
created: 2026-11-01T09:05:00Z
iteration: 1
---

# 代码评审报告

**结论: REJECT。两个可复现的状态机问题仍需修复；child compaction 修复已复核，不重复列为阻塞项。**

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-subagent-final-state`
- 分支: `fix/subagent-child-run-tracking`
- 输入: 本目录 `001-spec-output.md`、`002-code-report.md`，以及 `engine/adapter/specs/2026-11-01-openclaw-child-run-completion/spec.md`。
- 增量比较: `f475365f56a39b30eb21ffd7dd76584e81d454b8` 到工作区，包含未跟踪的 `child_runs.py` 和新测试。
- 整体 PR 比较: 本地 `github/dev...HEAD` 及其 merge-base 到工作区。`github/dev=6fb00420cf553fe36cde9f6eca4204a1f4d07200`；merge-base 为 `6212471de435239f18acdbfe796e5b85cd342cb4`。没有把 dev 后续提交误报为本分支删除。
- 本轮增量 8 个文件：2 个实现、2 个测试、4 个说明/任务文档；包含原分支历史的整体范围为 16 个文件，均不含本报告。
- 已纳入审查中的即时更新：`parent_covered`、child compaction/provisional state、subagent session 作为父 session 的过滤修复。
- 实现快照 SHA256：`gateway_client.py=46ee33cf4056f41392ddadfe9613ca5d7fdb9dd995d4b55b73bdbeaddecd369f`；`child_runs.py=2ed9ce85f6e2f3a1adf746cd984c47933ffcbf4d11d599184e1357c58c8e34e2`。后续并发修改需重新验证。
- Reviewer 只写本报告；无实现/测试编辑、Git mutation、额外 worktree、部署或网络写入。测试产物位于 `/tmp/child-run-independent-review.*`。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|------|------|------|
| 正确性 | FAIL | F1：旧父 final 能更新 delivery coverage；F2：early-final fallback 绕过零 child completion 的授权限制。 |
| 安全性 | PASS | 未发现新增凭据暴露、注入或可确认的跨 session 安全漏洞；F2 作为同 session 的状态关联错误报告。 |
| 性能 | PASS | provisional 身份缓存上限 128；未见本次范围内明显性能回归。 |
| 代码风格/范围 | PASS | Adapter 内部状态追踪，未修改 OpenClaw 或外部帧契约；gateway 文件当前 1000 行。 |
| 测试覆盖 | FAIL | 现有 client 用例 171/171 通过，但缺少 F1/F2 的组合时序行为断言；高行覆盖率不能排除这两个错误。 |
| ACI 覆盖率门禁 | PENDING | 当前实现尚未提交，未提供该实现对应的远端 job；本地候选指标见下。 |
| 静态检查 | PASS | Ruff 与 `git diff --check` 实跑通过；无本次新增 unused import/local 或已检查的 whitespace 告警。 |
| 外部系统边界日志 | PASS（本次范围） | 无新 RPC/外部协议；保留现有收发链路日志。新增状态日志使用业务身份/计数，不输出 message/tool result；已有行为测试包含私有 child 输出不进入日志的断言。本轮不是远端边界日志认证。 |

### 具体问题列表

#### F1 [P1] 重复的旧父 final 会提前覆盖直接交付的 child

- 文件: `engine/adapter/src/engine/community/openclaw/client/gateway_client.py:539`；关联 `child_runs.py:55`。
- 严重程度: 必须修复。
- 原因: `cover_completed()` 在判断 `waiting_for_subagent_resume` 之前调用，仅检查 foreground identity 和 compaction。父 final 已经延迟、且尚无后续 continuation 时，重复收到同一个旧 final 仍把已完成 child 标成 `parent_covered`。之后它不再参与 `unannounced_count`，因此另一个 child 的 announce 就能结束流。
- 通过真实 `chat_stream` 和现有 `StreamProbe` 复现；全程没有父 continuation，也没有 A announce：

```python
for child in CHILDREN[:2]:
    stream.spawn(child)
stream.parent_final("old")
stream.child_terminal(CHILDREN[0])
stream.parent_final("old")  # 重复旧 final，仍处于 waiting_for_subagent_resume
stream.announce(CHILDREN[1], text="B delivered")
events = await stream.result()
```

- 实际输出身份/状态：`[(parent-run, delta), (parent-run, delta), (announce:v1:agent:main:subagent:beta:child-run-beta, final)]`；流已结束。
- 预期: 重复旧 final 不改变 A 的 delivery coverage；继续等待有效父 continuation/final 或 A 的匹配 announce。正常的“直接父结果覆盖 A，再由 B announce 收尾”路径必须保留。
- 建议: 把 direct coverage 更新限制到符合时间边界的普通父 final；尚在等待 post-defer continuation 的重复 final 不得更新 coverage。补上述反例及正常 mixed-path 正例。

#### F2 [P1] pre-ACK 的 early-final fallback 在零 child 完成时授权无关 run

- 文件: `engine/adapter/src/engine/community/openclaw/client/gateway_client.py:362`；关联 `gateway_client.py:515`。
- 严重程度: 必须修复。
- 原因: 新的 resume 路径检查 `children.has_completed`，但下面的 `pending_early_final` fallback 没有同样限制。ACK 后同步重放缓冲帧时，旧空 final 尚未被消费者清除；即使随后已注册 pending child 并延迟父 final，无关同 session assistant 仍被 fallback 接受，并被 `early_final_followup_run` 加入 `allowed_run_ids`。其后 error 会作为 hard stop 终止当前流。
- 复现设置: `OPENCLAW_EARLY_FINAL_GRACE_SECONDS=0.05`，传入 `on_ack`，expected/ACK run 为 `parent-run`。在 fake `send_request` 返回 ACK 之前，通过 `_fire_event` 顺序发送下表；均为父 session `agent:main:child-completion-parent`，没有发送任何 child terminal 或 announce。

| 顺序 | event | runId | payload 关键字段 |
|------|-------|-------|------------------|
| 1 | chat | parent-run | `state=final`，无 text/message（空 early final） |
| 2 | chat | parent-run | `state=delta, stream=tool, data=_accepted_sessions_spawn_data()` |
| 3 | chat | parent-run | `state=final, text=old` |
| 4 | agent | unrelated-run | `stream=assistant, data={delta: unrelated}` |
| 5 | chat | unrelated-run | `state=error, errorMessage=unrelated failed` |

- 返回 `ResponseFrame.ok_response(..., {runId: parent-run, status: started})` 后，实际输出顺序为 `ACK, delta, None, error`；输出身份为 `[(parent-run, delta), (unrelated-run, None), (unrelated-run, error)]`。无关 run 的非终态和错误都进入当前流。
- 预期: 已注册 child 尚无一个完成时，拒绝无关 run 的 assistant/approval/terminal；保留没有 child 时原有空 early-final followup 兼容行为。
- 建议: 子任务等待状态必须同时约束 early-final fallback 和 allowed-ID promotion，不能只约束 `authorize_parent_resume`。补 pre-ACK 同步重放与非零 grace 的行为断言。
- 此分支在旧代码已存在，但新 spec 明确要求它不能绕过 child-completion 前置条件；属于本次状态机接线的契约缺口。

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|------|--------|------|------|
| R-01 | 精确 accepted child 身份；duplicate/foreign 隔离 | PASS | run/session 配对，重复注册和终态不重复结算；provisional 身份只有 exact accepted 后才进入正式计数。 |
| R-02 | parent continuation 不减少 pending execution | PASS | pending 只依赖 child.completed；三个 child 一次 parent resume 等既有用例通过。 |
| R-03 | child 完成后不可释放旧父 final | FAIL | 直接释放已受限，但 F1 能通过 coverage 更新间接提前结束 mixed fan-in。 |
| R-04 | child 未完成不得授权无关同 session run | FAIL | F2；默认 grace=0 的新 probe fixture 没有覆盖该组合。 |
| R-05 | exact v1 announce、重复 final 和 fan-in | FAIL | 纯 announce 与正常 mixed 正例通过；F1 破坏 mixed 的 delivery boundary。 |
| R-06 | error/abort 只结算对应 child；parent hard stop | PASS | client 回归通过，child 错误没有作为父错误转发。 |
| R-07 | child compaction 中间 final 不代表执行完成 | PASS | 最新修复已验证：registered child、accepted 前 provisional child、pre-ACK provisional child 三种探针均不提前结算；真实 child final 后正常完成。不把此前已修复版本重复列为 blocker。 |
| R-08 | parent compaction 不更新 direct coverage | PASS | 中间 parent compaction final 后只收到 B announce 时仍等待 A；A announce 后正常完成。 |
| R-09 | 父 session 本身可为 subagent session | PASS | 最新 `ChildRuns(parent_session_key)` 跳过规范化后的父身份；独立真实流及默认 agent alias 探针通过。 |
| R-10 | ACK、approval、inject、empty final 兼容 | FAIL | 既有回归通过，但 early-final/pre-ACK/child 的组合存在 F2。 |
| R-11 | 已知协议能力边界准确 | PASS | 不要求 WS 证明模型消费；child 完成后无法区分无关同 parent run 的限制已明确接受。F1/F2 均在已有时间状态足以拒绝的情形下复现，不要求协议变更。 |

### 测试与静态检查证据

在 `engine/adapter` 使用已有 `.venv`，禁用 bytecode 和 pytest cache，未安装依赖：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
COVERAGE_FILE=/tmp/child-run-independent-review.coverage \
.venv/bin/python -m pytest -p no:cacheprovider \
  src/engine/community/openclaw/client/tests \
  --cov=engine.community.openclaw.client.gateway_client \
  --cov=engine.community.openclaw.client.child_runs \
  --cov-report=term-missing \
  --cov-report=xml:/tmp/child-run-independent-review.coverage.xml \
  --junitxml=/tmp/child-run-independent-review.junit.xml -q
```

- 结果: **171 passed**，1 条现有 pytest 插件弃用 warning；无 failed/skipped。
- `child_runs.py`: 102/102 executable lines；`gateway_client.py`: 428/499；合计 **530/601 = 88.19%**。仅为这两个模块的 focused coverage，不是全 Engine 覆盖率。
- Ruff: `--no-cache --preview --select F,E117,E203,E701,E702,E712,W291,W293,W605`，上述四个实现/测试文件全部通过；`git diff --check` 通过。
- 另通过临时内存探针复现 F1/F2；未把这些确认缺陷的探针算进 pytest 通过率，也未写入测试源码。

### ACI 覆盖率证据

- **ACI evidence PENDING**。当前源码包含未提交/未跟踪内容，没有与该工作区实现一致的不可变 PR head 或远端 job。没有运行会把当前源码 coverage 错配给旧 HEAD 的 `report_check.py`；旧 PR 的历史绿灯不能代表本次实现。
- 当前 HEAD: `f475365f56a39b30eb21ffd7dd76584e81d454b8`；目标 base 为 GitHub dev，需 rebase 后以真实 base/head 重新采集。
- 本地用例候选: **171/171 = 100%**；skipped=0、failed=0；阈值 100%。
- 本地 focused lineCoverage 候选: **530/601 = 88.19%**；阈值 70%；全 Engine/远端分子分母尚不可得。
- 新增量可执行变更行候选: **128/130 = 98.46%**（`f475365f5` 到工作区，包含新增 child 文件）。
- 整体可执行变更行候选: **158/160 = 98.75%**（`6212471de...` 到工作区，包含新增 child 文件）；阈值 90%。按 diff 的新增行与 coverage XML 可执行行求交计算，并非正式 PR ACI 结论。
- 未覆盖变更行: `gateway_client.py:505`（拒绝 parent identity 的 accepted spawn 日志/路径）；`gateway_client.py:786`（pending compaction timeout reason）。另外必须补 F1/F2 的行为组合断言，不能用已有行命中代替。
- 远端 ACI job: **PENDING**。全 Engine 回归、最终 rebase/PR checks 由原编码/编排流程继续完成。

## 整体结论

**REJECT**。修复 F1/F2 并加入有行为断言的回归测试后复审。child compaction 与 subagent-parent 修复已纳入本轮通过项，不需重复修复。后续源 PR 仍需以最终 head 完成 tc-engine-regression 与远端 ACI 门禁；本评审不执行部署。

### Iteration 1 补充验证（F1/F2 修复前）

- 已复核最新 `_log` helper：增加 system/direction/operation/elapsedMs 和 parent business ID，无新增 RPC；不改变 F1/F2 的判断。
- 最新 client 回归 **173/173 通过**；failed=0、skipped=0。局部可执行行 **535/606 = 88.28%**（child_runs 107/107、gateway 428/499）；新改动行候选 **133/135 = 98.52%**，整体改动行候选 **163/165 = 98.79%**。Ruff 再次通过。此组数据更新上面的本地测量，ACI 仍为 PENDING。
- 该次实现 SHA256：gateway `46ee33cf4056f41392ddadfe9613ca5d7fdb9dd995d4b55b73bdbeaddecd369f`；child_runs `43f1df89b7bb032bdd087dd17112ecd2846fd2efd6c1d42dc163298cbe3e3045`。
- 用户已接受 F1/F2，计划先补 RED，再限制 waiting/compaction 状态下的 coverage 更新，并在 children.runs 非空时关闭 early-final fallback/promotion。尚未对该修复实现作 PASS 判断。
- 保留本轮历史 **REJECT**；修复后的结果应在本文件追加 iteration 2，不覆盖本轮缺陷、复现或原始验证记录。用户提供的 ACLI exact-dev 基线失败不作为本轮新增 blocker；Linux 远端 CI 仍需独立验证。

---

## Iteration 2 独立复审（2026-11-01T09:11:26Z）

**结论: REJECT。F1/F2 已修复；新增恢复游标存在一个剩余状态机缺陷 F3。** 本节为最新结论，保留上方 iteration 1 历史原文。

### 复审快照与验证范围

- 已完成 rebase 的分支: `rebase/subagent-child-runs-on-dev`。
- Base: `6fb00420cf553fe36cde9f6eca4204a1f4d07200`；Head: `9425f65ad7faaae7b7ce7f7df404083bc17da146`。
- 已核对 rebase 前后实现/测试 SHA256 相同：gateway `457ca0c5f178b0403a05aae095875a743d239d5efd925cb2daf352d568ace9a4`；child registry `d843c896c988c76ab40f87e96316974958e101b07f062b53cc30a175b3a02411`；child tests `92dd63cebd9651d618a5daf9bedeb907f89ee21075a76e10b1f5a095f2e9c88f`。
- 复核三个 F1/F2 guards、`resumed_parent_run_id`、`ChildRuns.matches_parent`，以及 mixed announce/direct、child/parent compaction、审批首帧、same-original-run 与连续两个新 run 恢复。
- 只追加本报告；未修改实现/测试或 Git 状态。按用户要求，本轮写入完成后停止报告写入，等待新的明确复审版本。

### 原问题与固定维度

| 项目 | 结论 | 证据 |
|------|------|------|
| F1 旧 final 更新 coverage | FIXED | `cover_completed` 现要求非 waiting、非 compaction；原反例保持流打开，A announce 后才结束。 |
| F2 early-final 授权绕过 | FIXED | 匹配与 allowed-ID promotion 均要求 `not children.runs`；原反例及 8 种 pre-ACK 无关 assistant/delta/approval/final/abort 变体均被拒绝，真实 child 完成与有效 resume 后正常结束。 |
| 新游标的普通旧 final 隔离 | PASS | 没有旧非终态插入时，连续两个新 resume 中的原 run/前一 run final 都被丢弃；同原 run 恢复仍正常。 |
| 正确性/duplicate 隔离 | FAIL | F3：旧父 run 的重复 spawn 可以回退恢复游标。 |
| 安全性、性能、范围/风格 | PASS | 未发现新增可确认安全漏洞或明显性能回归；无外部协议修改；gateway 为 998 行。 |
| 测试覆盖 | FAIL（行为缺口） | 最新现有用例 176/176 通过，但未覆盖旧 run duplicate spawn 回退游标；独立探针仍可复现 F3。 |
| 静态检查 | PASS | 同上 Ruff 规则在四个实现/测试文件实跑通过；`git diff --check` 通过。 |
| 新增入站状态日志 | PASS（本次范围） | 保留 system/direction/operation/elapsedMs 和业务身份投影；没有新 RPC，未引入 message/tool 内容日志。 |
| ACI | PENDING（远端） | 精确 base/head 的本地 focused 检查通过；不是全 Engine 或远端 job 的 PASS。 |

### F3 [P1] 旧父 run 的重复 spawn 能把恢复游标切回旧 run

- 文件: `engine/adapter/src/engine/community/openclaw/client/gateway_client.py:370`，特别是 `:385` 的游标赋值；相关 stale final guard 在 `:534`。
- 严重程度: **必须修复**。
- 原因: `authorize_parent_resume` 在 spawn 去重之前处理所有 tool continuation。已有新 run `resumed-parent`，其 final 因另一 child 未结束而再次进入 waiting 后，旧 `parent-run` 的重复 `sessions_spawn` result 仍满足 same-parent/has-completed/resumable 条件，先把 cursor 改回旧 run 并清除 waiting。稍后 `children.register` 才发现它只是重复注册，无法撤销已经发生的授权变化。
- 独立内存探针通过真实 `chat_stream` 重现，使用现有 `_stream`/`CHILDREN`/`PARENT_RUN` helper，不编辑测试文件：

```python
async with _stream(monkeypatch) as stream:
    for child in CHILDREN[:2]:
        stream.spawn(child)
    stream.parent_final("old root")
    stream.child_terminal(CHILDREN[0])
    stream.continuation(run="resumed-parent")
    stream.parent_final("second child pending", run="resumed-parent")
    await stream.assert_open()
    stream.child_terminal(CHILDREN[1])
    stream.spawn(CHILDREN[0], run=PARENT_RUN)  # 旧 run 重复原 accepted result
    stream.continuation(run="resumed-parent")
    stream.parent_final("fresh resumed result", run="resumed-parent")
    events = await stream.result()
```

- **实际结果 A**: 最后是 `state=error, errorMessage=Chat stream timeout`。有效 resumed final 被新的 stale guard 丢弃；两个 child 均已完成，pending=0。
- **实际结果 B**: 将上述最后的 continuation/final 改为 `stream.parent_final("stale root final", run=PARENT_RUN)`，立即输出旧 root final 并结束流。新游标 intended stale-final 隔离被同一重复事件绕过。
- 预期: 已被新恢复 run 取代的旧 run duplicate 不改变 cursor、waiting、coverage 或最终结果归属；真实当前恢复 run 的 final 能结束流。
- 最新 worker fixture 把重复 spawn 改为来自当前 `resumed-parent`，该正例通过；它不覆盖这里保留旧 `PARENT_RUN` 的重放反例。因此不把较早并发测试失败冒充最终 176 项中的失败，但也不能用 fixture 更新消除实际状态机问题。
- 建议: 在授权/更新 cursor 之前区分已被取代的旧允许 run 与当前 run/新的候选 continuation，保证旧 run replay 或重复 tool result 不能回退 cursor。保留第一次同原 run 恢复和合法新 run 恢复；为以上 timeout 与旧 final 被转发两个分支增加断言。
- 这不是“child 完成后无法区分未知同 parent run”的协议限制：这里有明确的旧 run 身份、当前恢复身份和重复的 accepted child identity，Adapter 已有足够状态识别。

### 最终本地验证与 ACI 证据

- 最新完整 client suite: **176/176 = 100%**，failed=0、skipped=0，1 条已有 pytest 插件弃用 warning。新 child testcase 文件为 46 个参数展开用例。F3 是另外的独立失败行为探针，不计入这组 pytest 分子/分母。
- 局部 executable lineCoverage: **540/611 = 88.38%**（child_runs 110/110、gateway 430/501）。
- 在 rebase 后的精确 base/head 上运行仓库 `singlebox/ci/report_check.py`：

```bash
PYTHONDONTWRITEBYTECODE=1 engine/adapter/.venv/bin/python \
  singlebox/ci/report_check.py \
  --junit /tmp/child-run-independent-review-iteration2.junit.xml \
  --coverage /tmp/child-run-independent-review-iteration2.coverage.xml \
  --source-root engine/adapter/src \
  --base 6fb00420cf553fe36cde9f6eca4204a1f4d07200 \
  --head 9425f65ad7faaae7b7ce7f7df404083bc17da146 \
  --min-case-pass-rate 100 --min-line-coverage 70 \
  --min-change-line-coverage 90
```

- 本地 focused 门禁命令 exit 0：casePassRate **176/176 = 100%**；lineCoverage **540/611 = 88.38%**；changeLineCoverage **169/171 = 98.83%**。三项阈值分别为 100%、70%、90%。覆盖率通过不能替代 F3 的业务断言。
- 未覆盖变更行: gateway `:500`（parent identity spawn 拒绝路径）、`:784`（pending compaction timeout reason）；child registry 无缺失执行行。
- 全 Engine 最终回归与远端 ACI job: **PENDING**。已证实的本地 ACLI exact-dev 基线失败不列为本轮新增 blocker，也不推断 Linux job 已通过。
- **唯一剩余必须修复项: F3。Iteration 2 最终结论 REJECT。**
