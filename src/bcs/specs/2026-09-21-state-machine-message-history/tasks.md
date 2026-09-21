# StateMachine 消息历史持久化实施任务

- 依据：[spec.md](./spec.md)；范围为 `src/bcs/`。上一轮实现及完成标记已全部回滚；本轮保留已有文件布局，重新完成 T01–T03 的实现；T05 已实现并通过本轮读取验收。验证结果与发布阻塞项见文末。
- 按 4 个开发任务推进，测试与契约更新随对应改动完成。T04 历史回填与对账已按用户决定移除，保留其他任务编号。发布操作单列，删除旧读取路径作为后续收尾。
- 首次双写上线只要求 T01–T03 及其发布条件完成；messages 查询可以后续开发、分开发版。双写运行一段时间后直接通过配置切读，不新增旧消息 backfill / check 工具或要求全量历史对账报告。
- 切读后保留 messages 中已有旧记录；仅存在于 Run / Node、尚未写入 messages 的旧状态机消息不再展示。等待双写不会自动补齐这些旧记录，此缺口是本次明确接受的范围边界。
- H01–H35 是验收覆盖清单，可以由同一组测试覆盖，不要求 35 个独立测试，也不重复执行没有受后续改动影响的检查。

## 开发顺序

| 任务 | 内容 | 前置依赖 | 完成后具备的能力 |
| --- | --- | --- | --- |
| T01 | 统一消息身份与展示 | 无 | 新旧来源能归一化，保留权限与客户端展示 |
| T02 | 完成必要存储改造 | T01 | 确定可靠写入方案，复用原有表结构，解决窗口影响 |
| T03 | 实现可靠双写 | T01、T02 | 可以发布双写，继续使用 runtime 查询 |
| T05 | 实现 messages 查询及切读验收 | T01–T03 | 双写观察后通过配置切换查询来源 |

此表是建议实施顺序。实际切读要求双写运行正常、新查询验证通过，并在发布说明中明确旧消息展示边界；不要求迁移双写启用前的全部历史。公共游标升级、工具事件持久化、额外消息类型不在范围内。

## T01：统一消息身份与展示

主要范围：`bcs-domain`、`bcs-service-api`、`bcs-message`、Runtime 历史拼装、`bcs-app-session` 及两套 HTTP adapter。对应 spec 第 5、6、8.1、9 节。

- [x] 用现有样例固化 panel、prompt、V1 / V2 output、旧 Human response、published_result 的身份、角色和可见性；覆盖 StateMachine、混合会话及 Bot / Human 视角，不另建一套测试框架。
- [x] 统一结构化逻辑身份、稳定消息 ID 和公开 ID 投影，复用已有 producer key、长 ID 哈希及真实存量别名；不同节点、attempt、Run 或发布结果不按正文去重。
- [x] 新旧来源按同一身份选定持久记录后再做权限与展示筛选，最后排序、limit；不可见记录不能回退到更宽松的动态记录。统一最新 N 条倒序结果，保留两套 API 的返回形态和现有公共 before 边界。
- [x] 状态机逐消息历史只在存储及 metadata 中保留 Run 关联，顶层不引入 Run 级分组；普通 chat / published_result 继续现有分组与实时回复关联。用现有 Workbench 转换链及 CLI 验证多 Human、多节点、Loop 和多 attempt。

完成条件：共享身份及投影可被后续写入、恢复和读取复用；重复展示和前端错误合并有回归证据，owner / audience 不扩大。窗口相关存储调整由 T02 接入，不能因为先完成投影就开启新增双写。

## T02：完成必要存储改造

主要范围：`bcs-service-api`、`bcs-collaboration-store`、`bcs-message-store` 与历史读取服务。对应 spec 第 7、8.2、12 节。

- [x] 复用现有 checkpoint，状态接受与冻结 payload 同事务提交；幂等投影直接复用原 `append_message_with_id`。
- [x] 保留单一物理序号及 join_seq，在普通 Chat 查询中跳过标记为新增投影的记录，保留旧 V2 行和普通删除空号的窗口语义，owner / audience 独立过滤。
- [x] 撤除两列、三个索引及窗口初始化命令；恢复 Session store、投递鉴权和正常追加原有契约，不新增 JSON 计数器。
- [x] 在原表结构下重跑 Memory / SQLite / 真实 MySQL 契约，验证幂等、故障恢复、窗口、查询计数与执行计划；本轮结果见文末。

完成条件：无新增 DDL 或初始化前置步骤；查询窗口不扩大权限、不被新增投影挤占，访问成本和预算失败行为有证据。原双序号相关验证仅保留为历史记录，不作为当前方案完成证据。

## T03：实现可靠双写

主要范围：`bcs-collaboration-runtime`、已选定的 store 能力、`bcs-config-api` 及 bootstrap。对应 spec 第 5、7、10.2、11、14 节。

- [x] 接入 V1 / V2、Bot final、HTTP / Channel Human 回复及 HumanInput 激活：冻结正文、接受时间、身份和受众，正在 judge 的回复也保存。Completed 确认同一记录，重试、取消和终态不删除已接受回复。
- [x] 落实 T02 选定的原子性与幂等保证：CAS 失败不产生消息，数据库写失败传播错误，重复或迟到回调不创建第二份记录。首次启用时先固化活跃节点已有事实，再允许覆盖或重试。
- [x] 完成必要故障恢复。若采用 checkpoint，复用同一投影逻辑处理 Pending，包括终态 Run，并落实有限批量、退避和关闭新写入后继续恢复；若采用同库事务，以事务回滚、重试和提交后响应丢失测试证明可靠性，不额外增加新投影 worker。
- [x] 保留已有 opening / publication 的本地历史修复能力，复用原身份和事实；补消息不得重发 Bot / IM、重跑 judge 或确认网络投递成功。
- [x] 接入默认关闭的持久化配置及必要指标，记录配置生效方式。默认仍为 `persistence_enabled = false`、`read_source = runtime`；messages 读取尚未实现的版本必须明确拒绝该配置，不能静默回退。完成双写持续失败、并发及重启验证。

完成条件：新消息可靠持久化，runtime 查询不重复、不扩权、不损坏前端展示或普通聊天窗口；与本次写入改动相关的真实数据库、契约、架构及 Singlebox 检查通过。此时可以进入“双写上线”清单，无需等待新查询实现或 H22 的切读隔离测试。

## T05：实现 messages 查询及切读验收

主要范围：`bcs-message`、`bcs-app-session`、Runtime 历史入口、两套 HTTP adapter、配置及 bootstrap。对应 spec 第 9、10、11、15 节。

- [x] 共用来源选择及 T01 投影：messages 模式只从 MessageRepo 重建状态机消息内容，保留必要鉴权和混合会话原有普通聊天策略，不用运行表、定义、checkpoint 或 Bot 原生历史补缺。
- [x] 用调用即失败的运行来源 Repo 完成 H22 隔离测试，并验证两个 API、各角色视角、分页及现有 Workbench / CLI 消费；流程面板 API 不在内容读取隔离范围内。
- [x] 验证无回填切读边界：messages 中已有旧记录仍可读；仅存在于运行来源的旧消息不再展示，不触发查询时补写或 fallback；双写期间新增消息按原保证持久化。
- [x] 通过 `state_machine_history.read_source = runtime | messages` 按环境切读，不新增切读 API。明确现有配置加载的生效方式；没有热更新能力时通过兼容版本实例重启生效，不为本需求额外建设热更新机制。
- [x] 拒绝 `messages + persistence_disabled`，验证切回 runtime 仍保留已存旧 attempt；回滚查询或重开 persistence 保持查询侧窗口补偿、不丢已接受事实、不直接降级旧二进制。
- [x] 补齐新增读取及切读的集成和访问成本证据，更新契约、CONTEXT 和发布说明，明确双写启用时间与旧消息展示边界；核对 H01–H35 无覆盖缺口，已有通过证据可复用，改动影响的部分再补测。

完成条件：messages 查询通过内容来源隔离、无回填边界、消费者回归及读取回滚验证。双写观察期结束并完成切读清单后，可宣告目标环境仅从 messages 读取状态机聊天历史；不宣告双写启用前的全部历史已迁移。

## 发布清单

### 双写上线：T01–T03 完成后

- [ ] 确认这次上线涉及的身份兼容、窗口安全、幂等、原子性和故障恢复验证通过；不要求 T05 或全部切读验收提前完成。
- [ ] 部署支持新增投影标记及窗口补偿的兼容版本；确认旧读取实例退出后再开启双写。不执行 DDL、窗口初始化或旧消息回填。
- [ ] 配置 `persistence_enabled = true`、`read_source = runtime`，按已确认的配置生效方式发布；核对活跃 Run、重启恢复、新旧记录去重及普通聊天窗口。记录环境的双写启用时间，保留必要指标和记录，不执行旧消息回填。

### 切读：T05 通过且双写观察期结束后

- [ ] 双写持续运行一段时间，确认新增消息、故障恢复及 Pending 投影正常，无未处理的持续写入失败、身份冲突或长期积压；发布说明明确旧消息不会自动补齐。不设置自动切换定时器，不增加全量历史迁移门槛。
- [ ] 满足 spec 第 11.2 节门槛后，将 `read_source` 配置改为 `messages`，保持 persistence 开启；观察接口、消费者、权限、延迟及数据库负载。
- [ ] 完成验证环境的读取回滚演练。实际需回退时改回 `runtime`，保留兼容版本、必要恢复能力和窗口语义，不删除或重写消息数据。

## 后续收尾

- [ ] 支持范围内的环境切读稳定、读取回滚期结束后，独立清理运行来源历史拼装、选择分支及无消费者的配置，并更新契约和测试；保留运行调度、流程面板及必要诊断数据。

此收尾不阻塞本次 4 个开发任务或双写上线。新路径已通过隔离测试且目标环境完成切读，即可验收本次功能；旧路径删除继续作为明确待办跟踪。

## 验收覆盖与验证记录

| Spec 场景 | 主要任务 | 验证时点 |
| --- | --- | --- |
| H01、H02、H03、H04、H05、H06、H07、H08、H09、H10、H11、H12 | T02、T03 | 双写上线前 |
| H13、H14、H15 | T01、T03 | 双写上线前 |
| H16、H17、H21 | T01、T05 | 先验证 runtime 兼容，切读前补 messages 路径 |
| H18、H30 | T01、T02、T03 | 双写上线前 |
| H19、H20、H29 | T05 | 切读前验证无回填边界及新增消息收敛 |
| H22 | T05 | 切读前，不阻塞双写上线 |
| H23 | T03 | 双写前验证既有事实恢复及本地补写 |
| H24、H27、H34 | T02、T03、T05 | 双写前验证新写入与开关，切读前补读取回滚 |
| H25、H26 | T02、T03、T05 | 各阶段验证本次涉及的数据库、并发及负载路径 |
| H28、H35 | T02、T03 | 双写上线前 |
| H31、H32、H33 | T02、T03 | 双写前验证新增投影、恢复与查询侧窗口补偿 |

若 T02 改变存储机制，先在 spec 中更新与机制有关的 H 描述，再保持本表对应；不能仅将不再适用的测试标为通过。

每个任务完成时记录实际 commit / PR、测试命令和结果、H 编号、数据库类型及未验证项。执行最接近改动的 Cargo / 契约测试，按仓库要求补对应架构、配置和 Singlebox 检查；真实 MySQL、现有 Workbench 转换链等必要证据不能被静态检查替代。保留已有文件布局，不因项目行数上限擅自拆分或搬迁已有实现及测试；新增职责明确的功能文件不搬迁无关代码。不运行全局格式化，不修改无关文件；若检查因既有文件长度失败，如实记录，不放宽检查。

> 以下早期记录描述当时实现。涉及新增索引、双序号、初始化的结论已被文末无 DDL 方案取代；未删除历史验证记录，也不将其视为新方案已通过。

### 2026-09-21：回滚后重新实施 T01（未提交）

- 已回滚上一轮 36 个已跟踪文件的实现改动，移除 68 个新增实现文件，并清除旧完成标记及实施记录；保留 spec 和任务计划。
- 从原文件布局重新接入共享身份 / 投影、持久记录优先的权限与归并、最新页顺序及 canonical key 批查。保留普通 chat / published_result 的分组方式。仅增加本任务需要的 client-key 索引，不包含窗口列或初始化命令。
- 未拆分已有文件，未搬迁原有测试；server、Session store、Cargo 依赖及锁文件均无改动。新增文件仅承载共享功能、新增回归场景、消费者 fixture 和必要迁移。
- 遵循用户明确取舍保留已有文件布局。原有超长文件继续保留；MySQL message store 从 997 行增至 1,057 行，未为了行数限制拆分实现或放宽检查。未运行全局格式化。

| 本轮验证 | 结果 |
| --- | --- |
| `cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs-domain -p bcs-message-store -p bcs-message -p bcs-collaboration-runtime -p bcs-app-session -p bcs-http -p bcs-admin` | 1,090 项通过、0 失败；5 项外部 MySQL 测试默认忽略，其中完整迁移链另行执行如下 |
| 临时 MySQL 8.4 的 `full_mysql_migration_chain_applies_and_preserves_history -- --ignored` | 1 项通过，覆盖全链、历史前缀升级、重复执行及 5,000 行数据下单 key / 200 key 索引查询计划；未连接业务数据库 |
| `node --test src/bcs/tests/consumer/state_machine_history.cjs` | 2 项通过，验证实际 Workbench 转换函数和现有 CLI |
| Session / StateMachine V1 OpenAPI pytest | 19 项通过 |
| 架构静态检查 | port purity、forbidden symbols、store boundaries、interceptor chain 四项通过；依赖 / 导入 / 命名检查的 15 条失败与隔离 HEAD 基线完全相同 |
| `git diff --check` | 通过 |

以上为 T01 完成时的阶段记录；T02、T03 的后续实施和补充验证见下。T05 仍未开始，T04 已移除。未提交、推送或发布。


### 2026-09-21：T02 / T03 实施（未提交）

- 选用既有 checkpoint 方案。同库直接写 messages 虽少一次确认，但需要将消息身份兼容、窗口分配和消息仓储 SQL 重复引入协作仓储，或新增跨 Repo 事务契约；本次复用已有协作事务和后台扫描，不引入通用事务协调器或新 worker。
- T02：MySQL 029 / SQLite 030 合并必要列与索引，覆盖普通追加、事件追加、delivery admission、Session 创建 / 加入和窗口权限消费者；保留物理序号。交付有界 `initialize-window`（显式环境 / Session、dry-run、调用总批次预算、锁内复核、中断续传）。没有修改已提交迁移。
- T03：V1 / V2 Bot final、HTTP / Channel Human 回复与 prompt 激活保存不可变事实；Node CAS、checkpoint、原事件及 judge 输入保持原子性。消息幂等追加后确认，写失败传播；关闭新双写后仍恢复 Pending，终态、旧 attempt 和取消后的已接受回复保留。原 opening / 已成功发布结果可仅修复本地消息。
- 首次启用需先排空旧版本已接受但缺少接受时间的 Running / Judging 输入；遗漏时明确报缺少来源证据并阻止覆盖，不能声称这类旧输入可以无损在线自动补齐。
- 默认 `persistence_enabled = false`、`read_source = runtime`；通过现有配置及兼容实例重启生效，无切读 API。当前明确拒绝 messages 配置。T05 和生产发布清单保持未完成。
- 成本：接受事务三条 SQL，新消息投影查重一次 + 三语句事务 + 确认一次；正常成功不回读刚写入的 checkpoint，运行时接受前另有一次幂等事实查询。恢复每页一个查询、最多 100 份 payload、串行投影、5 秒节奏及最高 30 秒退避。正常、重复确认、最大分页和故障路径有计数 / 边界断言；真实 MySQL 验证并发及执行计划，未做生产规模连接池和 P99 锁等待压测。
- 未拆分或搬迁已有实现 / 测试，未全局格式化，全部仓库改动在 `src/bcs/`。新增文件承载本需求的新功能和回归测试；原有文件长度问题不通过拆分或放宽检查处理。

| 验证 | 结果 |
| --- | --- |
| 核心模块整轮回归：runtime、collaboration-store、message-store、message-flow | 951 项通过、0 失败；1 项外部 MySQL 测试另行执行 |
| 恢复扫描专项 | 9 项通过，覆盖 100 条上限、5 秒节奏、主从切换与关闭取消 |
| 真实 MySQL 8.4 完整迁移链及窗口专项 | 1 项通过，含并发追加 / 加入、事务回滚、有界初始化、旧行空号与代表性消息索引计划 |
| 真实 MySQL 协作契约（Text / Prepared） | 1 项通过，含 103 条同 Run checkpoint 分页、原子接受、迟到拒绝、并发确认、原协议回归；修复原生 JSON 比较差异 |
| 真实 MySQL Pending 查询计划 | 5,000 行 fixture 使用现有复合索引；EXPLAIN ANALYZE 返回 100 行，无 filesort；此结果不替代生产压测 |
| SQLite 文件关闭 / 重开 | 消息事务故障后重开数据库、关闭新双写仍补齐 Pending 并完成节点；包含在核心模块回归中 |
| Workbench / CLI 消费者 | 2 项通过 |
| Session / StateMachine OpenAPI | 19 项通过 |

覆盖 H01–H18 的本阶段写入及 runtime 兼容部分，以及 H23、H24、H25、H27、H28、H30–H35 的相关存储 / 恢复行为；H26 已验证有界批量、查询计数及小规模真实并发，生产吞吐和尾延迟不在本次证据内。T05 的无回填边界、读取隔离和正式切读验证仍待完成。


最终补充验证：

- 受影响模块完整 Cargo 测试共 2,690 项通过、0 个用例失败、31 项按原条件忽略。并行构建共享产物曾使 `bcs` / `bcs-collaboration-runtime` / `bcs-http` 文档测试产生 E0460 / E0463；清理被覆盖的契约 crate 构建缓存后，已串行重跑三个 `--doc` 目标，全部通过（`bcs` 中 2 个示例按原标记忽略）。
- 启用双写的真实 Provider 流程发现并修复接受 SQL 遗漏 `waiting_provider` 状态的问题，条件与已有派发 / judging 契约保持一致。补充 SQLite / MySQL 对有无 judge 两种 Provider 回复的回归；最终 collaboration-store 全套 112 项通过、0 失败、1 项外部 MySQL 测试默认忽略，真实 MySQL Text / Prepared 专项另行执行通过。
- 统一 Singlebox 已执行并生成标准报告：Backend acceptance 54 通过、3 失败，失败均为测试 Bot 的 BaaS 发布失败；BCS 初次运行 650 通过、2 失败、1 条未计入通过，失败为一秒 Provider 回调超时和 Loop 启动 CAS 冲突。将两个场景在隔离数据库副本与端口上重跑：默认关闭双写时 36 项断言通过；启用双写并完成上述修复后，36 项也全部通过，20 个历史 checkpoint 均为 Delivered。未把重跑结果改写成首次全量 gate 通过。
- Singlebox BCS 行覆盖率 41.47%、方法覆盖率 36.06%，均达到原门槛；HTTP endpoint 156/156、CLI 54/54。标准 artifact verifier 已执行，仍因首次 E2E / Backend acceptance 失败而拒绝报告；Backend 的 files / harness / bot_collaborator 覆盖也未达完整 gate 门槛。这些是发布阻塞项，本次不修改无关 Backend / BaaS 或门槛。
- 架构检查的已输出 173 条不同失败与隔离 HEAD 基线完全一致（175 次失败输出），没有新增失败项；配置验证 130 项通过，port purity、forbidden symbols、store boundaries、interceptor chain 通过。完整入口中的 workspace 测试发现性检查未完成：与本次构建冲突后停止了该进程树，未声称完整 gate 通过。其余 protocol / PR / waiver / public-api / baseline 五项按原脚本分别因缺目录、非 PR 环境、缺工具或缺基线 ref 跳过。
- 最终 `git diff --check` 通过。检查了全部 58 个新增 / 修改源文件行数：新增源文件最大 711 行；12 个已有文件仍超过 1,000 行，按用户要求保留布局，不拆分、不调整 allowlist。已跟踪文件 diff 为 843 行新增、532 行删除；没有文件搬迁，未包含 `src/bcs/` 之外的仓库改动。
- 测试用 MySQL、隔离 BCS 与 Mock 已清理。Singlebox 曾停止本地原服务，现已恢复前端和未修改 HEAD 的 BCS；原本地数据库仍停留在迁移 29，未向它应用本次迁移。

T02、T03 的开发项及本次新增行为验证已完成；全量发布门禁仍有上述失败 / 未完成项，发布清单保持未勾选。T05 messages-only 读取尚未实施，不能将本轮结果解释为已完成切读或可以跳过发布门槛。

### 2026-09-21：移除旧消息回填任务

- 按用户决定移除尚未实施的 T04 及其管理工具、全量对账和切读依赖，保留 T05 编号；改为双写观察后通过现有配置切读。
- 同步 spec 的目标、发布条件、历史展示边界与 H19 / H20 / H29；已有 T01–T03 实现及完成标记不变。窗口初始化和已接受事实的故障补写继续保留。
- 本次仅调整 spec 和任务文档，未修改代码、配置或数据，未执行回填或切读；新增读取验收仍由 T05 完成。
- 验证：任务依赖、H01–H35 映射、本地链接及空白检查通过；T01–T03 完成标记不变。仅文档调整，未重跑代码测试。


### 2026-09-21：T05 messages 查询与三个本地 Session 验收（未提交）

- 两个 Runtime 历史入口在读取任何运行来源之前按同一配置选择 MessageRepo；Full/Bot 排除 Human prompt，Participant 在 SQL LIMIT 前按冻结受众过滤。没有切读 API，配置通过兼容实例重启生效；`messages + persistence_enabled=false` 启动校验失败。
- 两套 HTTP 入口保留原鉴权与响应结构，共用原投影和身份归并。混合会话普通聊天保留原策略，旧会话的状态机正文只取已存消息；不访问运行来源补缺，不在查询时回填。状态机名称使用冻结值，不逐条读 Bot。
- 新增调用即失败的 workflow DB 与 Bot 原生历史测试，以及只读 SQL 守卫。覆盖纯状态机、混合 Full、混合 Participant、未授权用户、同毫秒内部游标 / 公开时间戳边界、隐藏消息不占分页、数据库错误传播、Human prompt / response、Loop 旧 attempt 切读及回滚。
- 只对用户提供的本地数据库做一致性快照并查询，未更改原数据库、配置，未重启或停止用户服务。没有旧消息回填，没有对实际环境切读。

本地双写核查：运行实例使用的 `bcs-config.toml` 和 `bcs-config-local.toml` 没有开启 `state_machine_history.persistence_enabled`，因此仍为 false / runtime；快照中 `operation_kind=history_message` 为 0。没有双写启用时间，不能将这三次操作记作新双写成功证据。

| 用户 Session | 消息表中的状态机记录 | 运行来源情况 | messages 模式两套 API 返回数 |
| --- | --- | --- | --- |
| 自定义协作完整视角，`…5123:31249fcc` | 原 V2 panel 1 条、output 4 条 | 4 个已完成节点，5 个 skipped | 均为 5，无重复 |
| 任务协作完整视角，`…bf12:b1dec947` | panel 1 条、最终发布 chat 1 条；新增节点 output 为 0 | 一次性状态机 6 个已完成节点 | 均为 39，含普通聊天，无重复 |
| 任务协作参与者视角，`…aee8:57ca655c` | panel 1 条、最终发布 chat 1 条；新增节点 output 为 0 | 一次性状态机 7 个已完成节点 | 均为 29，按参与者视角过滤，无重复 |

这是无回填边界验收：已有消息仍可读，仅存在于运行来源的旧 prompt / output 不会因 T05 自动出现。新增双写正确性由开启 persistence 的 Human / Loop / Provider 及故障恢复测试验证；用户这三个旧 Session 不作为该证据。

验收方式：`bcs --test state_machine_history_read` 使用实际 SQLite 仓储、应用服务和两套路由，通过进程内 HTTP 请求查询；没有启动恢复扫描、Bot 或外部发送。手动快照测试通过 `BCS_HISTORY_ACCEPTANCE_DB` 和 `BCS_HISTORY_ACCEPTANCE_SESSIONS` 指定数据，默认 ignored，不把用户数据库或正文纳入仓库。该测试禁止写事务及所有 StateMachine/Collaboration 表访问，校验结果一致、ID 去重、分页和未授权拒绝。

访问成本：状态机正文页为单个有界 SELECT；三个实际 Session 的 Legacy / OpenAPI 请求分别为 5/3、5/3、7/5 次 SQL（含必要元数据，仓储缓存生效）。真实 MySQL 5,000 行混合数据命中 `idx_session_type_created`，估计 502 行并使用 filesort；不声称大规模延迟或扫描成本已完全验证。T05 没有新增 migration、管理 API、历史对账任务或后台 worker。

本轮不运行会启停当前用户服务的统一 Singlebox，使用只读快照路由验收和相关模块回归；上轮完整 Singlebox / 架构入口的失败及未完成项继续保留，发布清单不勾选。


| T05 本轮验证 | 结果 |
| --- | --- |
| `cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs -p bcs-message -p bcs-api-http -p bcs-http -p bcs-app-session -p bcs-config-api -p bcs-message-store -p bcs-collaboration-runtime -p bcs-admin` | 2,016 项通过、0 失败、33 项按条件忽略，包含新增 HTTP / 核心读取隔离用例与 Loop 回滚 |
| 最大 1,000 条页面专项：`-p bcs-message-store --test conformance_message_repo history_read` | Memory / SQLite 共 2 项通过；1,001 条新增输出及混合噪声下，最大页、lookahead 和复合游标续页正确 |
| `-p bcs --test state_machine_history_read snapshot_messages_http_acceptance -- --ignored --nocapture`（通过上述环境变量传入快照） | 1 项通过，三个真实 Session 的两入口结果、分页、鉴权、来源隔离全部通过；未启动业务服务 |
| 已编译 `bcs-admin` 测试目标 `full_mysql_migration_chain_applies_and_preserves_history --ignored --nocapture`，临时 MySQL 8.4 | 1 项通过，完整迁移链、窗口、Full/Participant 查询、JSON audience、环境隔离、同毫秒游标与查询计划；临时容器已清理 |
| `node --test src/bcs/tests/consumer/state_machine_history.cjs` | 2 项通过，使用实际 Workbench 转换函数和既有 CLI |
| Session / StateMachine OpenAPI pytest | 19 项通过 |
| 配置校验 gate | 130 项通过，messages 要求 persistence 开启，默认 runtime 不变 |
| 架构静态检查 | port purity、forbidden symbols、store boundaries、interceptor chain 通过；依赖、导入、命名的 14 条失败与隔离 HEAD 基线一致，没有新增失败 |
| 范围 / 空白 / 行数 | `git diff --check` 通过，新增文件无尾部空白；所有变更均在 `src/bcs/`。检查 63 个新增 / 修改源文件，新文件最大 720 行；12 个已有超长文件保留原布局，不拆分、不放宽规则 |

部分测试初次在沙箱内因禁止本机监听失败，已在允许随机本机监听的测试进程中重跑通过；未关闭测试、未改写门槛。T05 开发项完成，实际环境仍未开启新双写及切读；发布清单继续未勾选，不能将这次只读验收解释为观察期已完成或旧历史已完整迁移。没有提交或推送。


### 2026-09-21：按用户决定收敛为无 DDL 实现（未提交）

本节取代前述关于双序号、三个新增索引及窗口初始化的设计与完成结论。

- 用户明确授权“允许撤下源码中的这两份迁移，数据库不动”。撤下未提交 MySQL 029 / SQLite 030 及注册、两个窗口字段、三个索引和 `state-machine-history initialize-window` 命令。未修改本地保留库、迁移记录或现有消息，未启停用户服务。
- 保留原 `session_seq` / `current_msg_seq` 和 `participant_join_seq`。状态机投影复用 `append_message_with_id`；普通 Chat 在查询中根据已有 JSON 的 `history_schema_version=1` 标记补偿窗口，保留旧 V2 行和普通删除空号。恢复 Session store、投递鉴权及 delivery 写入中仅服务于双序号的改动。没有拆分或搬迁已有代码。
- 移除 checkpoint 中多余的 `occupies_participant_window` 字段，由冻结消息 metadata 表达新增投影的语义。原子接受、幂等投影、Pending 恢复及 messages-only 查询保留。
- 本地保留 SQLite 仍有实验列、索引和 030 记录，新代码不依赖它们。撤除前只读检查：`history_message` checkpoint 为 0，两个计数器没有分叉。后续复用此库升级同编号迁移前需单独处理实验版本，不能覆盖 checksum。
- 用户已有的两个配置文件改动保持原样。所有变更仍限制在 `src/bcs/`，未提交或推送。

| 无 DDL 版本验证 | 结果 |
| --- | --- |
| `cargo test -p bcs-message-store -p bcs-message -p bcs-collaboration-store -p bcs-collaboration-runtime` | 535 通过、0 失败、1 项真实 MySQL 测试按条件忽略，随后单独执行通过 |
| `cargo test -p bcs -p bcs-admin -p bcs-app-session -p bcs-http -p bcs-api-http -p bcs-config-api -p bcs-domain -p bcs-message` | 1,692 通过、0 失败、33 项按条件忽略；两组计数含部分重复测试，不作去重总数 |
| `cargo check -p bcs-admin --bin bcs-admin` | 通过；移除初始化命令后，消息 / Session 仓储依赖仅用于测试 |
| 临时 MySQL 8.4 完整原 028 迁移链、消息事务和查询 | 1 项通过；显式断言无两个窗口列和三个新增索引，覆盖并发幂等、插入失败回滚、JSON audience 与复合游标 |
| 临时 MySQL checkpoint / Provider / Loop 契约 | 1 项通过，Text 和 Prepared 两种协议；只在隔离空库运行 |
| 原表结构 SQLite 的 Memory / SQL 窗口与负载契约 | 包含在以上测试中；覆盖固定加入锚点、无 join_seq、旧 V2 不改标记、普通空号、audience、跨环境及失败传播；普通短窗口 1 次 SQL，1,025 投影为 3 次，16,384 投影为 33 次，超预算明确失败 |
| 三个实际 Session 的原表结构副本 HTTP 验收 | 1 项通过，副本移除两个列及三个索引；Legacy / OpenAPI 返回 5、39、29 条，身份、权限、分页和来源隔离保持一致；原始数据库未修改 |
| Workbench / CLI 消费者、Session / StateMachine OpenAPI 契约 | 分别 2 项、19 项通过；消费者测试本机监听限制通过隔离测试进程放行后重跑解决 |
| 架构 / 范围 | port purity、forbidden symbols、store boundaries、interceptor chain 通过；deps / imports / naming 的 15 条既有失败与本轮重新执行的隔离 HEAD 基线完全一致；`git diff --check` 通过 |

访问成本与边界：不新增索引后，5,000 行混合消息样本的 client key 单 key / 200 key 查询均选择原 `idx_session_type_created`，估计扫描 503 行；物理 ID 查询使用 PRIMARY。messages 历史查询估计扫描 502 行并 filesort，不能据此声称任意规模都满足延迟要求。普通 Chat 每批至多返回 512 个序号 / 标记，数据库仍解析候选 JSON；单次跳过超过 16,384 条补充投影时明确报错。投影序号和标记须随 Session 保留，硬删除投影无法与普通空号区分。

本轮检查 51 个新增 / 修改源文件，新 Rust 文件最大 679 行；10 个已有文件超过 1,000 行，遵循用户要求保留布局，未拆分或放宽规则。没有运行会启停产品栈的 Singlebox，前述全量发布门禁的已知失败与未完成项没有被本轮结果覆盖。生产规模吞吐 / P99、实际环境双写观察与切读发布仍未完成，发布清单保持未勾选。

本轮日志位于 `/tmp/bcs-no-ddl-{unit,http,mysql,mysql-checkpoints,snapshot,consumer,openapi}.log`；原始未提交源码备份位于 `/tmp/bcs-no-ddl-before.tar.gz`，用于追溯本轮撤除范围，不包含运行数据库。
