# BCS 嵌套固定 Loop 交接说明

- 交接日期：2026-09-22
- 接手工作区：`/Users/wuhao/Alipay/AvernetTemp`
- 接手分支：`quechao_dev_0922`
- 接手代码基线：`7b19ffbd08a83b4de2bf1e88ba4aee9c6d52d21e`
- 文档目录：`apps/bcs/specs/2026-09-21-nested-fixed-loops/`
- 状态：交接已接收并继续实施；实时任务状态与证据以 `tasks.md`、`validation.md` 为准。

## 2026-10-09 rebase 续开发更新

- 已先将本分支 rebase 到 `origin/dev` 的
  `154684d090e02b34a55b1df49dda7851db00883c`，原始交接基线仅作历史记录。
- 上游已将 `src/bcs` 和 `src/frontend` 分别迁移为 `apps/bcs` 和
  `apps/frontend`。所有功能改动、新增 fixture、seed 和文档已同步迁入新路径。
- rebase 只在 `bcs-config-api/CONTEXT.md` 出现一处文档冲突；已同时保留上游
  `UplinkConfig` 说明和本功能 `FixedLoopLimits` 深度合同。
- rebase 后 BCS 完整快速门禁、嵌套运行时、Provider live、strict
  OpenAPI/Event、panel 和 Frontend 定向/模块门禁均已重跑通过；准确命令和
  计数见 `validation.md`。
- T03/T09 仍只剩需要可销毁真实 MySQL 环境的持久化与容量测量；
  unified singlebox、artifact verifier 和发布/回滚演练仍未执行。

## 2026-10-13 rebase 与覆盖率更新

- 已无冲突 rebase 到 `origin/dev` 的 `1b9bfed2b88e545bcf4dfbeac700e2a35f89ad3a`，
  暂存与未暂存实现保留，未提交或推送。
- 已执行 BCS/Frontend/BCN coverage、panel、strict contracts、架构门禁、默认全模块
  Singlebox 和 artifact verifier；完整结果见 `validation.md`。整体不是全绿：
  初次 bridge WebSocket 单测有间歇失败、BCN attw 崩溃、架构门禁失败；Singlebox 还有
  BCSFuse 崩溃导致的 4 项 E2E 失败及 expert_chat 覆盖不足。
- 后续已修复 bridge 测试端串行读取导致的心跳超时，仅改测试；完整插桩单测
  6264/6264 通过（另 57 skipped，零重试）。GitHub 同款单测 coverage gate 本地通过：
  通过率 100%、整体行 85.03%、变更行 94.42%。expert_chat 命中上游已有的
  BaaS local_proc/ARCA-only 验收缺口，与嵌套循环无直接关系，但其 Singlebox 门槛仍失败。
- 两层 Bot 和三层 Human 嵌套 live E2E 通过。真实 MySQL 入口已补强为
  v1/v2 × Text/Prepared 并实际通过，T03 完成；T09 的容量实测及发布/回滚演练仍待完成。

## 交接内容与基线

先阅读 [spec.md](spec.md)，再按 [tasks.md](tasks.md) 的准备清单和依赖推进。
spec 是功能与兼容合同，tasks 是实现和验收清单；本说明只补充接手上下文，不替代两者。

两份文档从 `/Users/wuhao/Alipay/Avernet` 的同名目录原样迁入，源文件移除。
迁移不包含业务代码、测试产物、运行数据或 Git 提交；本目录文件尚未提交。

最近一次完整设计复核基于 `f49424bc4`。目标基线在其后增加两个 BaaS 提交；迁移前
逐字节核对的 19 个 BCS 定义、编译、快照、上下文、历史读写/恢复、配置与历史 spec 文件
均一致，文档相对引用在目标工作区有效。本次迁移没有在目标工作区重跑功能测试。
后续若基线继续变化，先复核受影响合同，不直接把旧测试结果当作新实现完成证据。

## 已确定的设计边界

- 沿用 compile-to-DAG，在创建 Run 前递归展开为有限叶子 DAG；不新增 LoopRun、子 Run、
  运行时 stack、调度器或数据库执行模型。两处现有嵌套禁止校验都需要修改。
- 默认最大深度 4，最外层算第 1 层，可配置正整数；每层最多 32 轮、body 直接节点最多 64、
  全计划最多 2048 叶子和 4 MiB 的默认预算保持不变。未选中分支也计入展开预算。
- 每层 entry/result 必须是直接普通 BotTask/HumanInput 叶子；子 Loop 可在中间或分支中。
  不支持跨层 break/continue、跨 scope 跳转、动态循环或子 Loop 直接充当父入口/结果。
- 完整 `loop_path` 区分所有祖先轮次，旧 loop 字段仍表示最近一层。
  previous result 仅来自当前 Loop 实例的上一轮，换父轮次后内层首轮重新为 null。
- 一套递归编译流程，实际嵌套使用 compiler v2；普通 DAG、单层新计划和历史 v1 保持原合同。
  v1 字节/hash/ID 不变，恢复和 rerun 读原快照，不重编，不套用后来收紧的编译 limits。
- `graph.loops` 以完整逻辑路径作为 map key，不增加重复 descriptor 路径字段。
  API/Event/消息/CLI/panel/Frontend 同步消费完整路径，UI 不重算选路。
- 新嵌套准入默认关闭。关闭它仍允许已准入 Run 恢复排空，总 Loop 开关须保持开启。
  预览不受执行准入阻挡：混合版本先保持深度 1，消费者和 reader 都升级后再开放预览/执行。

## Rebase 后必须接上的消息历史合同

最新代码已有状态机消息可靠持久化、补写恢复和基于 Session 创建时间的 cutoff 选读。
本次是扩展路径传播和回归覆盖，复用这些机制，不重新实现消息历史系统。

- 完整 execution 随接受 artifact 和不可变历史 checkpoint 保存；补写重放原正文、路径、
  受众和时间，不能读取最新 Node/plan 重建。Judge 失败及 retry 后保留旧 attempt。
- 复用现有消息身份和物理 ID helper；compiler v2 不要求升级 `history_schema_version: 1`
  或 checkpoint `schema_version: 1`，也不要求新 DDL。
- 投影未完成不能返回接受成功或派发依赖后继；Pending 补写覆盖终态 Run，关闭嵌套准入
  或历史 persistence 后也可恢复，重复补写不增加消息 sequence、不重复网络发送。
- messages-only 按 Session 创建时间分流，one-shot/rerun 不重置该时间；不得为补路径读
  Run/Node/plan，空页/错误不回退运行态。保留受众、混合会话 owner 和窗口补偿合同。
- 公共 panel/prompt/output 不引入顶层 Run 对话分组，避免 Workbench 合并不同节点或轮次；
  普通聊天与 published result 聚合保持原样。
- 数据库成本必须包含历史接受、消息追加/确认和补写扫描，并在相同历史配置下比较扁平 DAG。
  4 MiB 是 plan 上限，不是整个 Run 的消息/checkpoint/artifact 存储上限。

以上要求已并入 T03–T07、T09、T10，没有新增独立研发任务。

## 接手顺序与验收

1. 核对本工作区 HEAD、未提交更改及适用的根/模块指导；完成 tasks 准备清单。
2. 先做 T01 合同与配置、T02 递归校验/编译、T03 快照/持久化，再按任务依赖推进配置准入、
   上下文、DAG 恢复、消费投影、双前端、容量和真实服务故事。
3. 全部沿用 10 项任务及 31 个 C/R/F 用例的唯一主责任划分。不要新增与现有任务重叠的清单。
4. 复用两套故事：两层 BotTask、三层 HumanInput；故障场景由它们构造变体。
   spec 示例展开 17 叶子，指定轨迹为 15 Completed、2 Skipped。
5. 实施时建立同目录 `validation.md`，记录实际提交、命令、结果、成本和未验证边界；
   完成对应实现及验收后才能勾选。完整命令入口和发布/回滚清单见 tasks §4–7。

已执行证据来自原 Avernet 工作区、代码基线 `f49424bc4`：

| 检查 | 结果及边界 |
| --- | --- |
| fixed_loop_compiler / fixed_loop_snapshot / fixed_loop_context | 21 + 4 + 3 = 28 项通过；包含拒绝嵌套的旧测试，只证明现有基线 |
| runtime_progression 的 history filter | 55 项通过，175 项过滤；含引入的迁移测试，验证已有历史接受/补写/选读 |
| 文档检查 | spec/tasks 共 16 个本地引用有效；10 任务依赖无环，31 用例唯一归属；示例、代码块、命令入口检查通过 |

目标工作区实施前可复跑以下基线；下列命令在本次迁移中未执行：

```bash
cd /Users/wuhao/Alipay/AvernetTemp/apps/bcs
cargo test --offline -p bcs-collaboration-runtime \
  --test fixed_loop_compiler --test fixed_loop_snapshot --test fixed_loop_context
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression history
```

尚未验证嵌套执行、真实 MySQL、双前端和 live FO/完整覆盖门禁，不得将上述旧基线记作这些验收通过。

## 用户明确的实施范围约束

- 默认修改限制在上游重组后的 `apps/`；本功能仅涉及 `apps/bcs` 和
  `apps/frontend`，非必要不改根目录 docs/singlebox/.github 或配置。
- 已有文件保留布局，只做必要局部修改；未经用户明确要求，不拆分或搬迁既有代码/测试。
  此要求优先于项目按行数拆分的规则。新增职责可放入新文件，但不能借此重构无关实现。
- 检查改动源码行数；既有超长文件导致门禁失败时如实记录，不擅自拆分或放宽门禁。
- 不运行 `cargo fmt` 或全局 formatter，不修改历史迁移，不吞持久化错误。
- 长期名称表达稳定职责，不添加临时版本别名、双轨实现或无实际消费者的兼容层。
- 功能实施已进入收尾；真实 MySQL、singlebox、提交和发布仍分别按后续环境与验收推进。
