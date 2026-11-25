# BCS Bot Authority 事务可行性门禁证据（Task 0）

日期：2026-10-10

状态：已完成本地可验证部分；证据冻结后即可进入计划评审结论。本文件是实施准入交付物
（plan `2026-10-10-bot-authority-simple-implementation.md` Task 0），仅记录事实与结论，不
授权实施、提交或发布。

代码基线：`docs/bcs-bot-authority-simple` @ `fb5b95ed91`（spec/plan 编写基线为
`7a363f2c68`，差异仅为文档）。本文路径相对 `apps/bcs/`（文档路径另行标注）。

试验方式：真实 `LocalSqliteDbPlugin`（`crates/plugins/bcs-db-local`，5 连接池/文件库，
`journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout=5000`）在隔离 harness 中执行；harness
为未提交的临时集成测试文件（运行记录见 §3–§5，全文见附录 B），提交本证据前已删除。生产
文件零修改，未执行生产迁移。本文所有数字均来自附录 C 的实际运行输出，未运行的部分在 §5.4
逐项列为"未验证"。

判定结论（对应 plan §4 Task 0 的门禁）：**SQL 方案可用现有 DbPlugin steps 表达且被 SQLite
实证；但现有插件不能在运行时约束/取消一笔事务的总工作量，0.5 的超时重试出口缺少插件侧机
制，属于阻塞级发现，须按 §5.3 送人类评审后才允许进入 Task 2 写路径。**

---

## 1. 0.1 核对范围和责任

### 1.1 实现 PR 负责人

- 实现 PR 执行负责人登记为 **zhangwu.zh**（本分支 git user）。
- 登记依据：需求方在 2026-10-10 会话中明确要求执行本 Task 0，按规则登记分支实际负责人。
- 本 Task 0 未创建实施 PR；负责人登记在实施 PR 提交时随 PR 描述再次列出（豁免记录要求）。

### 1.2 2,500 行专项例外清单与复审日期

依据：[BCS-AUTHORITY-SIZE-2026-10-10](../../../../../docs/arch/waivers/2026-10-10-bcs-authority-file-size.md)
（已签署，2,500 物理行上限仅适用于本计划实际新增/修改的 `crates/**/*.rs`，含测试）。

**适用文件清单（计划 §1.2 的 12 个预计接入文件，按 `fb5b95ed91` 实测行数复核）：**

| 文件 | 基线行数（fb5b95ed91） | >2,500？ |
| --- | ---: | --- |
| `crates/services/bcs-edge-permission/src/lib.rs` | 3,797 | 是（需拆分） |
| `crates/services/bcs-edge-permission-store/src/lib.rs` | 2,293 | 否 |
| `crates/services/bcs-bot-store/src/lib.rs` | 3,936 | 是（需拆分） |
| `crates/services/bcs-bot-store/src/memory.rs` | 2,859 | 是（需拆分） |
| `crates/services/bcs-bot/src/application/bot.rs` | 1,608 | 否 |
| `crates/bootstrap/bcs/src/server.rs` | 6,976 | 是（需拆分） |
| `crates/bootstrap/bcs/src/config.rs` | 3,409 | 是（需拆分） |
| `crates/application/v1/bcs-app-session/src/lib.rs` | 1,818 | 否 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs` | 1,153 | 否 |
| `crates/services/bcs-eventing/src/subscription.rs` | 1,491 | 否 |
| `crates/services/bcs-message-flow/src/group_history.rs` | 1,429 | 否 |
| `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` | 2,682 | 是（需拆分） |

- 超限文件 6 个，`sum(max(lines - 2500, 0))` = **8,659 行**（计划 §1.2 按 `7a363f2c68` 记
  8,660 行；-1 行来自 `bcs-edge-permission/src/lib.rs` 3,798→3,797 的基线微差，非内容变化）。
- **复审日期：2026-11-10（或本计划完成之日，以先到者为准）。**
- CI 核对结论维持计划 §1.2：截至 `7a363f2c68`/`fb5b95ed91`，仓库无 BCS Rust 文件大小硬检
  查；无自动门禁不豁免文档规则，豁免依据是上述显式、限域、有期限决定。实施 PR 仍须链接
  豁免记录、列出逐文件最终行数并取得仓库评审。

### 1.3 补入 Group/Session Store 与共享 Run/内部 port 后的预算复核

计划 §1.1 的估算行已包含这两组（当时已修订过一轮）：

- `bcs-group-store`、`bcs-session-store`：行 6，"绕过展示缓存的权威资源权限快照，无 schema
  改动"，250–450 功能行 / 300–550 测试行 / 5–8 文件。
- `bcs-ws`：行 7，"writer、共享 Run 双 sink、alias/fallback、显式视角"，550–900 / 750–1,250
  / 10–16 文件，含 `crates/adapters/ws/bcs-ws/src/shared/run_channels.rs`（当前基线 579 行，
  将改为双 sink 注册）与 `ChatRunEventPort` String sender 的 Internal 限定。

按当前基线复核的调整结论：

1. **crate 集合不变**：快照落点 `crates/services/bcs-group-store/src/authorization_snapshot.rs`
   与 `crates/services/bcs-session-store/src/.../authorization_snapshot.rs` 均为现有 crate 内
   新文件，共享 Run sink 落点 `crates/adapters/ws/bcs-ws/src/shared/protected_delivery.rs`
   同理；无需新增 crate（计划"26 个既有 crate、0 个新增 crate"维持）。
2. **文件数与行数区间维持原估算**：`bcs-session-store` 现有 `memory.rs` 及 mysql 分段模块已
   大（会话侧快照文件将落在其旁），原 5–8 文件区间按新文件 + 少量修改仍成立；`bcs-ws` 的
   共享 Run 修改集中在 `shared/run_channels.rs` 与 `bot/dispatcher.rs`（后者已在 §1.2 需
   拆分清单内）。
3. **机械拆分预算不变**：6 个超限文件、8,659 行超额、机械改动暂估 20,000–35,000 行增删、
   10–20 个拆分文件（§1.2 原口径）。若实施期间新增 >2,500 行接入文件，按计划在 Task 0/1
   重新计数后更新本节。

### 1.4 入口矩阵（spec §3.3 授权事实逐项）

spec §3.3 的表格是唯一撤权边界；下表把它落到每一类入口的实际判定来源与验证位置。

| # | 授权事实（请求所依赖） | 判定来源 | 撤掉 Human A → Bot X 之后 | 验证位置 |
| --- | --- | --- | --- | --- |
| 1 | **Human→Bot（A 代表 X 操作/订阅 X 视角）**，无其他独立权利 | 新 `edge_grants` owner/manager 管理边（`grant_kind=owner/manager`、`approved`、`grant_ref_id=0`），经 `BotAuthorityPort.role/roles_for/can_manage` | 全部仅依赖该边的 HTTP/WS 入口失效：Bot 配置/状态/删除、Group CRUD/成员、Session 列表/详情/launch、文件、收藏、消息、邀请代操作、event subscription、WS 连接绑定重查 | spec §8；Task 6 `authority_cutover`（每入口断言所依赖的授权事实，不只断言布尔结果） |
| 2 | **Human self（A 自身的身份权利）**：A 的 Human 私有资产、A 自己的 Bot、A 是某资源的 creator/participant | 资源自身规则（如 Session 的 `created_by` 真实 actor、Group participant 记录），不经过管理边 | 保留：A 在 Group/Session 的 self/creator/participant 权利继续；`access_relation=self` 条目保留；A 的私有视角不被新 owner B 继承 | spec §8.0/§8.1/§8.3；Task 6（A 先缓存资源、撤管理边后 self 权利仍可用的反例断言） |
| 3 | **资源角色**：X 在 Group/Session 的 driver/originator/participant/manager-role | 权威资源快照 `GroupAuthorizationSnapshot`/`SessionAuthorizationSnapshot`（严格 Result，不回退展示缓存） | 与管理边无关地独立存在。管理 Group 中普通 Worker Bot ≠ 管理 Group；非 ManagerWorker strategy 的同名 Manager role 不放行 | spec §8.3；Task 6.4a strategy/scope 投影测试；bcs-group-store/bcs-session-store conformance |
| 4 | **Provider**：Provider 管理凭证 | 独立 Provider 认证与既有管理范围；退役实际操作者记为 `Provider { provider_id }` | 不由 Human 管理边派生；Provider attach/webhook/metadata/退役路径各自核对锁序（binding→Bot→转交→Human），不随管理边撤权失效 | spec §3.1/§7.1；Task 4/8；Provider-only actor 不能通过 sync/accept（Task 5 matrix） |
| 5 | **App（Gateway 可信应用 scope）** | 显式 allow 规则 `app_id+env+tenant+bot_ids+actions+target_owner_actor_ids`（精确匹配、无通配、空集合拒绝、`managers:sync/ownership:read/accept/reject`） — **已被 PR #2625 评审裁决改写为 `app+tenant+actions`（删 `env`/`app_id`/`bot_ids`/`target_owner_actor_ids`，以实例 env 绑定、按已验证 `app_name` 匹配）；本行仅作历史记录** | 与 Human 管理边无关；sync 与代办转交分别授予；sync-only 凭证不能 accept，转交 read/accept 必须有非空目标集合 | spec §3.1；Task 5 `authority_policy`；双准入接口 Human 拒绝不回退 App |
| 6 | **Bot-only（仅持 X 的 Bot 凭证，无 Human-bound 身份）** | 既有 Bot 自身认证契约（token=凭证） | 保留既有契约：管理边撤销不轮换已发出的 Bot token、不改变纯 Bot 调用的判定 | spec §3.3/§8 末段；Task 6（纯 Bot 调用恒走自身认证、混合凭证先验证 Human 控制权） |

补充三条同样冻结的判定（spec 原文）：

- 所有 ID 精确、区分大小写比较；数据库默认 collation 不得合并不同主体（§3.2-6）。
- Bot 被删除时同事务撤销 owner/manager 并关闭 pending；删除仍拥有存续 Bot 的 Human 拒绝
  （§3.2-7/8；lock order Bot→Human 预读复验）。
- 管理边不是 Bot friendship，不授予 permission profile/rules 的路由/消息许可（§3.2-5；Task
  6.6 runtime isolation）。

---

## 2. 0.2 静态事务步骤草图

以下草图全部使用**现有** `bcs-db-api` 原语（`DbPlugin::transaction(Vec<DbTransactionStep>)`、
`DbStatement::with_transaction_params`、`DbTransactionParam::query_result`、
`ExecuteChecked`），未引入 callback 框架。机制由 §3 的 harness 在 SQLite 实证。

### 2.1 事务外预读（仅参数准备）

- 分页读当前 manager 集合 S：`SELECT from_id FROM edge_grants WHERE to_id=? AND env=? AND
  grant_kind='manager' AND status='approved' ORDER BY from_id LIMIT 100 OFFSET ?`，逐页 100
  行，`<100` 行即停。结果仅用于排序去重、生成 S vs 输入的稳定差异（mutation_id + sequence
  按排序差异生成）、以及 input 中存在 revoked 行的分类（新增 vs 恢复）。
- 复用同 chunk 规则预查 input\S 的既有 revoked 行。
- 允许直接从 DB 读 owner 摘要以归一化排除 owner；owner 身份的**最终裁决在事务内 guard**
  （见下），预读不作为权限依据。

### 2.2 事务内 guard（全部成功后才写；SQLite/MySQL 通用）

事务步骤顺序（每步标注 SQLite/MySQL 方言差异）：

1. **Bot 锁行读**：`SELECT bot_id, env, ownership_version AS ov FROM bcs_bots WHERE env=? AND
   bot_id=?`。SQLite 走插件自动 `BEGIN IMMEDIATE`（任一步为写时）已经在 begin 处取得写锁；
   MySQL 草图必须加 `FOR UPDATE`（存续判断兼锁）。
2. **owner guard（授权 guard token）**：
   `SELECT t.owner_id, t.owner_n FROM (SELECT MIN(from_id) AS owner_id, COUNT(*) AS owner_n
   FROM edge_grants WHERE to_id=? AND env=? AND grant_kind='owner' AND status='approved') t
   WHERE t.owner_n=1`。
   恰好一行 ⇔ 恰好一条 approved owner（缺失或重复 ⇒ 0 行 ⇒ 完整性错误）。**该结果行必须被
   后续步骤以 `DbTransactionParam::query_result(owner_step, 0, "owner_id")` 引用**（本草图：
   count guard 与每个 chunk guard 以它做 `from_id<>?` 排除谓词）——少了这行，下一绑定步
   直接 `InvalidInput`，整事务回滚。
3. **count guard**：`SELECT f.c AS current_cnt FROM (SELECT COUNT(*) AS c FROM edge_grants
   WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0 AND status='approved'
   AND from_id<>?) f WHERE f.c = ?`（末参数 = |S|）。⇔ `count(current)=|S|`。空 S 必须
   验证 `count=0`（同一谓词，|S|=0 即空集合验证）。
4. **chunk guard ×** ⌈|S|/100⌉：每 ≤100 ID 块（排序后切块）
   `SELECT m.member_cnt AS ok FROM (SELECT COUNT(*) AS member_cnt FROM edge_grants WHERE
   to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0 AND status='approved'
   AND from_id<>? AND from_id IN (在?,...)) m WHERE m.member_cnt = ?`（块大小）。
   校验块内成员仍完整以 approved manager 形态存在（shape/status 内联在谓词里）；结合 count
   guard，多出来的主体必然使某块计数不可满足。
5. **写步骤**（§2.3），每条 `ExecuteChecked` 精确期望行数。
6. **sink（恒为最后一步，`ExecuteChecked` 期望 1 行）**：条件 CAS
   `UPDATE bcs_bots SET gmt_modified=<方言表达式> WHERE env=? AND bot_id=?
   AND (post-state manager count，绑定 owner 排除)= |input| AND ? IS NOT NULL ×（count guard
   + 全部 chunk guard 的 token）`。
   作用：(a) 事务内复验**写后**集合（count = |input|）；(b) 消费所有 guard token——**普通
   零行查询不会使事务失败**（§3 s1c），任何 guard 行若无后续步骤引用，其失败会被静默忽略，
   尤其是零差异事务（无任何块写步骤）只有一个 sink 可以消费它们。

**MySQL 特有（本地未验证，进 CI 门禁）：** 上述 guard 全部必须是**当前/锁定读**——RR 隔离
下普通 SELECT 读到的是事务开始时快照，不能参与 guard；草图在 MySQL 方言分支上给 step 1
加 `FOR UPDATE`，给 step 2–4 的 guard 加 `FOR UPDATE`（InnoDB 当前读），并由两连接测试证
明（§5.4 U1/U2）。`ExecuteChecked` 的 affected-rows 依赖"真正变化的写"：撤销/恢复都是
status 翻转（真变更，安全）；sink 在 SQLite 上同值更新也有 1 行（已实测 §3 s5a），**MySQL
非 found-rows 语义下同值 UPDATE affected=0，sink 的 SET 表达式必须是真正变化的值**，否则
幂等重放会被误判为失败（§5.3 D2）。

### 2.3 差异写步骤

按排序差异稳定生成 diff；`mutation_id` 每请求一个，`sequence` 单调递增，无差异则零审计。

- **S\input（撤销）**，每 ≤100 块：
  1. 先审计原状态：`INSERT INTO bot_authority_changes (...) SELECT <env,bot,mutation,seq基+ROW_NUMBER
     (OVER ORDER BY e.from_id)-1, 'manager_revoke', <service actor>, e.from_id, 'approved',
     'revoked'> FROM edge_grants e WHERE ...e.from_id IN (块)`，`ExecuteChecked` 期望=块大小；
     第一块额外内联 count 复验子查询并绑定 count-guard token（消费 step 3 的行）。
  2. 再撤销：`UPDATE edge_grants SET status='revoked' WHERE ...AND from_id IN (块)`，
     `ExecuteChecked` 期望=块大小。
- **input\S（新增/恢复）**，每 ≤100 块：
  - 新增：`INSERT INTO edge_grants (...)` 多行 VALUES + 审计多行 VALUES
    （`'manager_grant'`，before NULL），均 `ExecuteChecked` 期望=块大小。
  - 恢复：`UPDATE ... SET status='approved' WHERE status='revoked' AND from_id IN (块)` +
    审计（`'manager_restore'`，before 'revoked'）。
- **SQLite 绑定变量上限 999**（s12 探针实测：1,000 绑定报 `too many SQL
  variables`，990 绑定成功，附录 C）：审计多行 VALUES 为 10 参数/行 ⇒ 单语句
  ≤99 行可用，harness 实选 90 行子块（发现 F1）。多行 INSERT
  （6 参数/行）仍可用 100/块。MySQL 上限另测（U6）。
- 空差异时除 guard 外没有其他写步骤 → sink 是唯一写与 token 消费点。
- mutation+sequence 生成对同集合内以 `ORDER BY from_id` 决定，不依赖扫描顺序。

### 2.4 明确不做

- 不用普通零行 Query 当 guard（§3 s1c 实证：事务继续执行）。
- 不用 `with_transaction_stop_on_no_rows` 当回滚（契约与 §3 s2 实证：它会**提交已执行前
  缀**）。
- 不引入 Rust 回调事务框架（plan Task 0 Interfaces 约束）。
- 不做数量截断/部分成功；guard 失败、审计失败、集合替换全部整事务回滚。

---

## 3. 0.3 机制验证（真实 DbPlugin + LocalSqliteDbPlugin）

Harness：`crates/services/bcs-edge-permission-store/tests/task0_feasibility_scratch.rs`
（临时未提交、全部 `#[ignore]`，运行后删除；全文见附录 B）。表结构镜像
`migrations/mysql/014_edge_permission.sql` 的 `edge_grants` 列 + spec §4 的规划要素
（新唯一键含 `grant_kind`、部分唯一 owner 索引、`bot_authority_changes` 审计表、
`bcs_bots.ownership_version`）。运行命令：

```bash
cargo test -p bcs-edge-permission-store --test task0_feasibility_scratch -- --ignored --nocapture --test-threads=1
# 最终记录一：12 passed; 0 failed（2026-10-10，附录 C 首版）
# 最终记录二（评审修复，含 s11 重跑与 s12 变量上限探针）：13 passed; 0 failed（2026-10-10，
# 附录 C 现为该次运行逐字输出）
```

| # | 验证 | 结果（实际输出节选） | 结论 |
| --- | --- | --- | --- |
| s1a | owner 存在时 guard 全链提交 | 5 manager → input 3，8 审计+5 撤销+3 新增=16 行，tx 0ms | 草图可提交 |
| s1b | **owner guard 缺行**（删除 owner 边后同步） | `invalid database input: transaction parameter 2 references missing row 0 from step 1`；manager 保持 3 条未动 | `query_result` 引用必需 guard 行，缺行即整事务失败（owner/授权 guard token 机制成立） |
| s1c | 普通零行 Query 不是 guard | 零行 Query + Execute 的事务**提交**（marker=1） | 必须以 token 绑定消费 guard 行；零行查询单独不构成回滚 |
| s2 | `stop_on_no_rows` | 早期 `Ok` 返回 2 个 step 结果，**前缀已提交**（prefix_committed 在库，never_reached 不在） | 不能把 `stop_on_no_rows` 当作回滚手段（契约+实证一致） |
| s3a | **注入审计失败**（第二块审计 `UNIQUE(env,mutation_id,sequence)` 冲突） | `execute sqlite statement: UNIQUE constraint failed: bot_authority_changes...`；300 manager 全部幸存；mutation 审计仅剩注入的 1 行 | 审计失败整事务回滚，无半写 |
| s3b | **块 ExecuteChecked 失败**（同数量不同主体替换，300 内换 1） | `transaction condition failed: expected 100 affected rows, got 99` | 条件写按精确期望行数回滚；这不是只比数量——块 guard + ExecuteChecked 联合按集合（而非计数）定位缺失/替换的主体 |
| s4 | **同数量不同主体替换**（预读后 100 内换 1）+ 重试 | 预读方案失败 `expected 100 affected rows, got 99`，**零行持久化**；新预读重试成功（100 撤销+100 审计，200 行） | guard 在事务内复验当前集合；重试必须重新预读 |
| s5a/b | **零差异事务** + 陈旧零差异 | 幂等重复同一秒提交（sink affected=1，SQLite 同值更新仍记 1 行）；陈旧检测 `parameter 7 references missing row 0 from step 3` | 零差异不写审计；sink 消费 guard token 使陈旧集合不能静默通过 |
| s6 | **空 S 必须验证 count=0** | 真空同步通过；S 陈旧为空（实有 7 条）时 `parameter 6 references missing row 0 from step 2` | count guard 为空集合场景成立 |
| s12 | **SQLite 绑定变量上限探针**（F1） | 100 行 ×10 参（1,000 绑定）→ `execute sqlite statement: too many SQL variables`；99 行 ×10 参（990）→ 成功 99 行 | F1 由实测输出闭合：审计 VALUES 单语句 ≤99 行（harness 实选 90） |
| s10a | 两并发大事务 | 一例提交、另一例 guard 失败（count guard 行缺失） | 并发下陈旧 writer 被 guard 拒绝；SQLite `BEGIN IMMEDIATE` 序列化写者 |
| s10b/c | **锁等待与 busy_timeout=5000** | 持锁 4s：等 3.2335s 后提交成功；持锁 8s：**5.0052s 后失败 `database is locked`**（busy timeout 生效，失败发生在 BEGIN IMMEDIATE，未进入任何步骤，无半写） | SQLite 锁等待上界 5s 实测成立；失败即"锁级失败"，事务零写入 |
| s11 | **外层超时 ≠ 回滚**：10,000 存量事务（约 0.7 s）在 spawn 后约 1 ms、仍在运行中即 abort | 被丢弃 future 的阻塞事务**继续完成并提交**（manager 10,000→0，审计 10,000 行落库，732 ms 后可见，附录 C） | **外层 Tokio timeout/客户端断开不能作为回滚证据**（与 spec §7 担忧一致，实证） |

机制综合结论：

1. guard→token 消费链（bot 行 → owner 行 → count 行 → 块行 → sink）在现有 API 上成立，
   缺行/审计失败/计数不符/集合替换均整事务回滚。
2. `ExecuteChecked` 精确期望行数是**块内身份级**回滚的兜底（同一 attacker 场景在 count
   保平时由块 guard/块 ExecuteChecked 捕获——不比数量，比对集合）。
3. 零差异事务的 guard 失败必须有消费点；sink CAS 是当前契约下的最小方案（见 §5.3 D2 的
   MySQL remaining risk）。

---

## 4. 0.4 容量证据（真实 SQLite 插件）

统计口径：预读往返 = 事务外 query() 调用数；总步骤 = 传入 `DbPlugin::transaction` 的
`Vec<DbTransactionStep>` 长度（guard Query + ExecuteChecked 写 + sink）；行写 = edge_grants
与 bot_authority_changes 的实际插入/更新行数（由每条 ExecuteChecked 的期望行数合计，并逐
场景在提交后以实际 `COUNT(*)` 核对）；时长在测试进程内 `Instant` 计量（debug 构建，非性
能机，只取量级不自诩为性能指标）。

| 场景 | 预读往返 | 总步骤 | 行写（edge+audit） | 预读耗时 | 事务耗时 | 峰值内存（VmHWM 增量） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,001 → 空 | 11 页 | 37（3 guard + 11 chunk guard + 22 写 + sink） | 2,002 | 6 ms | **18 ms** | +0 MB（Δ0 KB） |
| 10,000 → 空 | 101 页 | 304（=3+100+200+1） | 20,000 | 424 ms | **701 ms** | +3.7 MB（Δ3,744 KB） |
| 10,000 → 1,000 不相交 | 101+10 页 | 334（=3+100+200+10+20+1） | **22,000**（撤销 10,000+新增 1,000+审计 11,000） | 427 ms | **713 ms** | +1.6 MB（Δ1,676 KB） |
| 10,001 → 空（guard 链余量：101 块） | 101 页 | 307 | 20,002 | 399 ms | 697 ms | +0 MB（Δ0 KB） |

附注：

- 上表为最后一次串行运行（附录 C 逐字）的数值。早先的并行乱序运行曾出
  现 59–816 ms 的运行间散布（测试进程无序竞争；该历史值只属于那次运
  行，逐字记录随首版提交 `92086f352d` 的附录 C 保留）。10,000 级事务耗
  时量级 **<1 s（SQLite debug）**，离 §5.1 的 10 s 目标有 ≥1 个数量级
  余量。
- 单条 SQL 100 参数 ≠ 总事务量：10,000→空 共 304 步、其中 200 条写步骤（每块
  审计+撤销各一）、20,000 行写；10,000→1,000 是 334 步、22,000 行写。**已按实际
  行数计，不是按 100 参数单句计。**
- 峰值内存为整个测试进程 VmHWM 增量（含 harness 自身分配，粗口径）：本
  次串行运行为 ≤+3.7 MB（Δ3,744 KB，见上表与附录 C）；早先并行乱序运
  行曾测得 +14.8 MB（历史观察，随首版提交保留作上界参考）。主要占用来自
  事务外预读的 S 列表（10,000 × 字符串）与步骤向量；事务内每次 Query
  物化 ≤100 行（块 guard）或 1 行（count/owner），与存量规模无关（这
  是草图刻意选择"预读在事务外、每步有界行数"的原因）。
- 锁等待：`busy_timeout=5000` 实测 5.0052 s 强制失败（s10c）；4 s 持锁下等待
  3.2335 s 后正常提交（s10b），与附录 C 逐字一致。

**容量级发现（F1）**：SQLite 默认绑定变量上限 **999**（s12 探针实测：
100 行×10 参的审计形 VALUES 报 `execute sqlite statement: too many SQL variables`，
99 行×10 参成功，逐字输出见附录 C），一行审计 10 参数 ⇒ 单语句 ≤99 行，
harness 实选 90 行子块。实施 Task 2 的审计语句参数化必须据此定块（或改为
逐行 INSERT）。MySQL 预编译上限以 CI 实测为准，不得假设。

---

## 5. 0.5 冻结容量记录

### 5.1 目标与实际生效位置

| 项 | 冻结值 | 实际生效位置 | 超出/失败时结果 |
| --- | --- | --- | --- |
| SQLite 锁等待 | **≤5 s**（目标；实测 5.0052 s 强制失败） | 插件连接 `PRAGMA busy_timeout=5000`（`LocalSqliteDbPlugin::new_file`），作用于 `BEGIN IMMEDIATE` 取锁 | `Backend("begin sqlite transaction: database is locked")`；**未进入任何步骤、零写入**，调用方获得确定错误 |
| SQLite 事务执行 | **≤10 s**（目标） | 插件级 between-step deadline：`DbPlugin::transaction_with_options(steps, options)` steps 间检查 `DbError::Timeout`（已实现，见 §5.3 CI 回填）；SQL 侧余量由 5.2 的工作量预算 + 容量证据兜底（实测 <1 s） | deadline 超出 → `DbError::Timeout`，经 Err 路径整事务回滚（SQLite 实证；MySQL 由 CI 门禁验证）；单条语句执行期内不可打断（契约已写明），调用方不得对 Timeout 重试 |
| 单请求步骤预算 | **≤340 步**（存量 10,000 + 输入 1,000 实测 334） | Store 构造事务步骤时按 §2 静态公式生成；默认配置拒绝 | 超预算属实现错误，不得静默截断 |
| 单请求行写预算 | **≤22,000 行**（实测同上） | 同上 | 同上 |
| 事务内单步行数 | guard/预读分页 ≤100 行/步；审计 VALUES ≤99 行/语句（999 变量上限，s12 探针实测；实现用 99） | SQL 草图固定 | 附录 C s12；MySQL 上限待 CI 实测 |
| 进程内存增量 | ≤ 约 15 MB（10,000 存量场景；含 harness 粗口径；本轮实测 ≤+3.7 MB，历史并行峰 +14.8 MB） | 事务外预读列表 + 步骤向量 | 超出须先复查预读是否在事务外分页完成 |
| 重试出口 | **本地重试仅允许在"确定已回滚"后**（plugin 返回 Err ⇒ 两后端已回滚，实测 s1b/s3a/s3b/s4/s5b/s10a） | Store 层 | 请求级不确定结果不得盲重试，按 spec §5.2 消费者契约处理 |

生命周期锁序（spec §7）在本草图中的落点：authority 无 binding 路径从 Bot 行开始（step 1
锁 Bot），不持 Bot 再请求 binding；MySQL 分支所有 guard 加 `FOR UPDATE`。

### 5.2 存量上界（deployment stock bound）

- **10,000 是最低容量证据，不是 manager 产品上限**（plan Task 0.4 原文）。首个部署的冻结
  存量上界取 **10,000**（实测 10,001 均整事务成功）。
- 实现要求（进入 Task 1/2 时复核）：sync 预读得到 |S| 超过配置的存量上界时，
  以明确错误拒绝整个请求，不做截断、不做部分覆盖。**公开的容量拒绝语义（错误
  码/HTTP 状态）需要先修订 spec §7，不允许实现者临场新增 manager 数量上限**——
  本记录只冻结"存量上界配置存在且拒绝即整请求失败"，不冻结错误码。
- 上界依据是本记录的容量证据；提高上界前须按同口径重测并更新本节。

### 5.3 超时/取消能力的门禁结论（阻塞级发现 D1/D2）

**D1 — 现有插件无法约束或取消一笔事务的总工作量。** 证据链（本节每条均为本记录实测
或源码核对，非推断）：

- `LocalSqliteDbPlugin`（`crates/plugins/bcs-db-local/src/lib.rs`）：事务在
  `tokio::task::spawn_blocking` 内一次性跑完全部 steps 再 commit；实现里没有 statement
  或事务级 deadline，没有取消点。`busy_timeout=5000` 只作用于取锁等待（s10b/c 实测），
  SQL 执行期间不受它限制。
- `MysqlDbPlugin`（`crates/plugins/bcs-db-mysql/src/lib.rs` + `manager.rs::with_transaction`）：
  `start_transaction(TxOpts::default())`——未设 isolation/timeout；语句执行无
  `max_execution_time`；`Err` 分支显式 rollback（该回滚语义已在 SQLite 侧由 harness
  验证；MySQL 侧同代码路径，列入 §5.4 U2 由 CI 验证）。
- s11：10,000 存量事务在运行中外层 abort future（spawn 后约 1 ms）后,阻塞事务
  继续执行并**提交**（审计 10,000 行落库,732 ms 后可见）。因此外层 timeout/客户端断开
  不能作为回滚证据。
- 总工作量的静态可约束性成立：步骤数与行写数均由 §2 公式按存量+输入规模枚举（§5.1
  预算），除单条恶意大 SQL 外，插件调用方构造的步骤集有界；但**运行时没有兜底**——
  若某单条语句因锁/IO 停滞，事务既不能被插件取消，也不能被调用方追认为已回滚。

**D2 — 零差异 sink 在 MySQL 上的写语义未决。** SQLite 同值 UPDATE 仍计 affected=1
  （s5a 实测两次）；MySQL 非 found-rows 语义下同值 UPDATE affected=0，会使幂等重放
  被 `ExecuteChecked` 误判失败。可选解：(a) 让 sink 的 SET 表达式真正变化；(b) 以
  DbPlugin 契约钉死 found-rows 语义；(c) 在 §5.3 最小插件方案里一并评审
  CheckedQuery（零写 guard 终验）。spec §4.2 未预留可随意变更的计数列 → (a) 需要
  一条 SQL 表达式层面真正变化的值（如基于 NOW(6) 微秒；列截断精度需 CI 验证），
  不能靠现有列保证。

**最小插件方案（须人类评审后才可进入 Task 2 写路径，不本端擅自实施）：**

1. **方案 A（推荐，最小契约扩展）**：`DbPlugin::transaction` 增加带选项的旁路（如
   `transaction_with_options(steps, options)`，`options.deadline: Option<Duration>`），语义：
   超过 deadline 的下一步开始前返回 `Err(DbError::Timeout)` 且事务整体回滚（复用现有 Err→
   rollback 路径）。两个后端当前架构即可实现：SQLite/MySQL 的步骤循环里在每步之间检查已
   用时间；残余风险=单条语句执行期内不可打断（须在契约上写明）。
2. **方案 B（后端原生，作为 A 的补充）**：SQLite 用 `rusqlite::Connection::interrupt()`/
   progress handler 实现语句内打断；MySQL 为 guard SELECT 附加 `MAX_EXECUTION_TIME` 并在
   deadline 触发后 kill 连接使回滚必然发生。改动面更大，作为 A 之后的加固项评审。
3. 无论 A/B：`DbError::Timeout` 加入契约错误枚举；Store 层重试规则保持 plan §4
   Task 2.3：仅对"确定已回滚"的本地冲突有限重试；timeout 错误**不允许**自动重试。

### 5.3.1 CI 回填（方案 A 已实现 —— 2026-10-10 Task 2）

人类评审结论采用 **方案 A**（bypass 缺省委托、两个 SQL 插件真实实现）：

- 契约：`bcs-db-api` 新增 `DbError::Timeout` 与 `DbTransactionOptions{deadline}`、
  `DbPlugin::transaction_with_options(steps, options)`（缺省实现委托 `transaction`
  显式忽略选项，重载方才真正约束）；契约文档写明残余风险 =
  **单条语句执行期内不可打断**，`Timeout` 不允许重试。
- SQLite：`LocalSqliteDbPlugin` steps 间 + commit 前检查 elapsed → `Timeout`，
  经 Err 路径整事务回滚。契约场景（`db_plugin_contract_tests`）：
  deadline 超出 → `Timeout` 且全部回滚（RED→GREEN，本 PR）；未超出 → 正常提交；
  原有 `transaction` 语义回归通过。
- MySQL：`MysqlDbPlugin` 同一实现（CI-verified，`conformance_db_plugin`
  届时执行同一契约场景）；本端无 MySQL，未本地验证。
- 调用方规则：Store 收到 `Timeout` 映射为确定性终止错误（不重试）。

### 5.4 未验证清单（本地环境原因，须 CI MySQL 门禁闭合）

环境核对：本机无 mysql 客户端/服务端（`which` 无 mysql/mysqld、3306 无监听），
`BCS_TEST_MYSQL_URL` 仅 CI 注入。以下按 plan §6 的门禁逐项挂起，**不得以 SQLite 结果冒充**：

| # | 未验证项 | 原因 | 覆盖门禁 |
| --- | --- | --- | --- |
| U1 | MySQL RR 隔离下普通 SELECT 快照参与 guard 的风险与 `FOR UPDATE`/current-read 修复；两连接（一读一写交错）证明 | 无本地 MySQL | plan §6 `bcs-db-mysql` conformance + Task 2 `authority_managers` MySQL suite |
| U2 | MySQL 侧整事务失败→rollback 的实证（同 D1 的 Err 分支路径） | 同上 | 同上 |
| U3 | MySQL `ExecuteChecked` affected-rows：`INSERT..SELECT`、IN-UPDATE、sink 的写语义（found-rows 类风险）与 NOW(6)/列精度 | 同上 | 同上；Task 8 `.4` 成本记录须含 sink 双方言断言 |
| U4 | `INDEX EXPLAIN`：`(env,to_id,grant_kind,status)` 前缀索引、owner 部分唯一索引（MySQL 可空生成列方案）、审计表 UNIQUE 的实际走索引计划 | 同上 | plan §6 Task 8 `authority_mysql` 四 suite |
| U5 | MySQL 上 10,000 存量级事务的容量/时长/锁等待（InnoDB 行锁、redo 增量未测） | 同上 | 同上（必须重跑 §4 全场景并更新 §4 表格） |
| U6 | MySQL 上 999+ 变量实际上限与预编译协议差异 | 同上 | 同上 |
| U7 | 生成列唯一索引（MySQL 8 可空生成列 owner 约束）在迁移链中的行为 | 迁移属 Task 1，未执行 | Task 1 `bot_authority_migration` + §6 迁移链测试 |

SQLite 侧仍有两条已知但**不阻塞门禁**的固化项：busy_timeout 只覆盖取锁（SQL 执行中途的
外部持锁不可重入——WAL 单写者下不存在该场景，多进程可写场景进入 CI 用例）；s10 的
sqlite3 CLI 持锁是进程外近似，真实"慢写者前台"竞争在 Task 8 两实例故事里复验。

### 5.5 门禁判定

- **SQL 可行性**：成立（SQLite 实证全部 guard/token/差异/回滚/容量机制，§2–§4）。
- **超时重试出口**：**当前不成立**——没有插件级超时；"超时后已回滚才允许本地重试"目前只
  能以"plugin 返回 Err ⇒ 已回滚"为据，而"外层超时"场景已实证**不可**作重试依据（s11）。
  按 plan Task 0.5，此为阻塞级发现：进入 Task 2 写路径前，须就 §5.3 最小插件方案（A 或
  B）取得人类评审结论，并按结论修订 plan/spec 的 §5.1 预算行。
- 本记录不声称 SQL/超时已获产品验证，不提出新 manager 上限、不引入数量截断。

---

## 6. 交付与本记录的复核钩子

- 本文档是 Task 0 唯一提交物；harness 删除，代码结转附录 B。
- Task 1 的 `authority/{mod,reads}.rs`、Task 2 的 `managers.rs` 实现须引用本文 §2 草图与
  §4 型号约束（审计子块行数、owner 惟一性 guard 的派生表形式、sink 消费所有 guard token）。
- Task 2/8 的 MySQL 套件须闭合 §5.4 U1–U6，并把结果回填本文（新增"CI 回填"节）或在差异
  大于 §5.1 预算时触发本记录复审。

---

## 附录 A 事务步骤草图 SQL（方言对照示例）

以"10,000 存量、输入 1,000 不相交"为例的 SQLite 实际形态（harness 即用此 SQL；MySQL 分支
差异加注）：

```sql
-- step 0: bot 锁行 (MySQL: ... WHERE env=? AND bot_id=? FOR UPDATE)
SELECT bot_id, env, ownership_version AS ov FROM bcs_bots WHERE env=? AND bot_id=?;

-- step 1: owner guard（token: owner_id）
-- MySQL: 对内层 SELECT ... 加 FOR UPDATE（当前读，RR 快照不得参与 guard）
SELECT t.owner_id, t.owner_n FROM
  (SELECT MIN(from_id) AS owner_id, COUNT(*) AS owner_n FROM edge_grants
    WHERE to_id=? /* ← step0.bot_id 绑定 */ AND env=? AND grant_kind='owner'
      AND status='approved') t
 WHERE t.owner_n = 1;

-- step 2: count guard（token: current_cnt；末参 = |S|）
SELECT f.c AS current_cnt FROM
  (SELECT COUNT(*) AS c FROM edge_grants WHERE to_id=? AND env=?
     AND grant_kind='manager' AND grant_ref_id=0 AND status='approved'
     AND from_id<>? /* ← step1.owner_id 绑定 */) f
 WHERE f.c = ?;

-- step 3..: 每块 ≤100 id 的 membership guard（token: ok）
SELECT m.member_cnt AS ok FROM
  (SELECT COUNT(*) AS member_cnt FROM edge_grants
    WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0
      AND status='approved' AND from_id<>? AND from_id IN (?,...,?)) m
 WHERE m.member_cnt = ?;

-- 撤销块 i：先审计（ExecuteChecked = k），再撤销（ExecuteChecked = k）
INSERT INTO bot_authority_changes
  (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id,
   subject_user_id, before_status, after_status)
SELECT ?, ?, ?, ? + ROW_NUMBER() OVER (ORDER BY e.from_id) - 1,
       'manager_revoke', 'service', ?, e.from_id, 'approved', 'revoked'
FROM edge_grants e
WHERE e.to_id=? AND e.env=? AND e.grant_kind='manager' AND e.grant_ref_id=0
  AND e.status='approved' AND e.from_id<>? AND e.from_id IN (?,...,?)
  -- 第一块内联 count 复验，消费 step 2 的 token：
  AND (SELECT COUNT(*) FROM edge_grants WHERE to_id=? AND env=?
         AND grant_kind='manager' AND grant_ref_id=0 AND status='approved'
         AND from_id<>?) = ?;
-- MySQL: 子查询内加 FOR UPDATE 等价谓词属当前读，由 U1 证明

UPDATE edge_grants SET status='revoked', gmt_modified=CURRENT_TIMESTAMP
 WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0
   AND status='approved' AND from_id IN (?,...,?);

-- 新增块：INSERT ... VALUES (≤100 行 × 6 参)；审计 VALUES ≤90 行 × 10 参
-- （SQLite 999 变量上限 → 审计语句必须 ≤90 行/语句，harness 实证）
INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status)
 VALUES (?, ?, ?, 'manager', 0, 'approved'), ...;
INSERT INTO bot_authority_changes (...) VALUES (?,?,?...), ...;  -- ≤90 行

-- 恢复块：UPDATE status='revoked'→'approved'（WHERE status='revoked' 保证真变更）

-- sink（最后一步，ExecuteChecked = 1）：写后集合复验 + 全部 guard token 消费
UPDATE bcs_bots SET gmt_modified = CURRENT_TIMESTAMP
 WHERE env=? AND bot_id=?
   AND (SELECT COUNT(*) FROM edge_grants WHERE to_id=? AND env=?
          AND grant_kind='manager' AND grant_ref_id=0 AND status='approved'
          AND from_id<>? /* owner token */) = ?  -- = |input|
   AND ? IS NOT NULL /* count guard token */
   AND ? IS NOT NULL /* chunk guard 1 */ ...
   AND ? IS NOT NULL /* chunk guard N */;
-- MySQL: SET 表达式必须真正变化（affected>0 语义），见 D2
```

审计 action 集（本草图覆盖 revoke/grant/restore；request/accept/expire/invalidate/
initialize/retire 属 Task 3/4 扩展）。

## 附录 B 试验 harness 全文（已删除的临时文件结转）

说明：`crates/services/bcs-edge-permission-store/tests/task0_feasibility_scratch.rs` 在产
出本记录后删除；结转如下以保证可复现。重新运行前置：在 workspace 内恢复该文件、不改生产
代码、跑 §3 的命令；用后删除。

```rust
//! ============================================================================
//! SCRATCH HARNESS — Task 0 feasibility experiments for the bot authority plan
//! (2026-10-10-bot-authority-simple-implementation, steps 0.2/0.3/0.4).
//!
//! THIS FILE IS A TEMPORARY, UNCOMMITTED EXPERIMENT HARNESS.
//! Every test is #[ignore]'d. It is NOT production code and must be DELETED
//! before committing anything; its code is preserved as an appendix of
//! `docs/superpowers/plans/2026-10-10-bot-authority-feasibility.md`.
//!
//! Run:
//! cargo test -p bcs-edge-permission-store --test task0_feasibility_scratch -- --ignored --nocapture
//! ============================================================================

use std::fmt::Write as _;
use std::path::Path;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bcs_db_api::{
    DbError, DbPlugin, DbStatement, DbTransactionParam, DbTransactionStep, DbTransactionStepResult,
    DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;

const CHUNK: usize = 100;

// ---------------------------------------------------------------------------
// Schema: authority-shaped tables created ONLY inside this scratch DB.
// Mirrors migrations/mysql/014_edge_permission.sql `edge_grants` columns plus
// the planned new unique key / partial unique owner index (spec §4.1) and the
// planned audit table (spec §4.4). Not a production migration.
// ---------------------------------------------------------------------------

const DDL: &[&str] = &[
    "CREATE TABLE edge_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        env VARCHAR(32) NOT NULL,
        from_id VARCHAR(256) NOT NULL,
        to_id VARCHAR(256) NOT NULL,
        grant_kind VARCHAR(16) NOT NULL,
        grant_ref_id INTEGER NOT NULL,
        rules TEXT,
        status VARCHAR(16) NOT NULL DEFAULT 'approved',
        originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any',
        originator_policy_data TEXT,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (from_id, to_id, env, grant_kind, grant_ref_id))",
    "CREATE UNIQUE INDEX uk_owner_per_bot ON edge_grants (env, to_id) WHERE grant_kind='owner' AND status='approved'",
    "CREATE INDEX idx_mgr_set ON edge_grants (env, to_id, grant_kind, status)",
    "CREATE TABLE bcs_bots (
        env VARCHAR(32) NOT NULL,
        bot_id VARCHAR(256) NOT NULL,
        ownership_version INTEGER NOT NULL DEFAULT 0,
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (env, bot_id))",
    "CREATE TABLE bot_authority_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        env VARCHAR(32) NOT NULL,
        bot_id VARCHAR(256) NOT NULL,
        mutation_id VARCHAR(64) NOT NULL,
        sequence INTEGER NOT NULL,
        action VARCHAR(32) NOT NULL,
        actor_kind VARCHAR(16) NOT NULL,
        actor_id VARCHAR(256) NOT NULL,
        subject_user_id VARCHAR(256) NOT NULL,
        before_status VARCHAR(16),
        after_status VARCHAR(16),
        gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (env, mutation_id, sequence))",
    "CREATE TABLE t0_marker (name TEXT PRIMARY KEY)",
];

async fn fresh_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    for ddl in DDL {
        db.execute(DbStatement::new(*ddl)).await.expect("create table");
    }
    db
}

async fn fresh_file_db(path: &Path) -> Arc<dyn DbPlugin> {
    let _ = std::fs::remove_file(path);
    let _ = std::fs::remove_file(path.with_extension("db-wal"));
    let _ = std::fs::remove_file(path.with_extension("db-shm"));
    let db: Arc<dyn DbPlugin> =
        Arc::new(LocalSqliteDbPlugin::new_file(path).expect("sqlite file db"));
    for ddl in DDL {
        db.execute(DbStatement::new(*ddl)).await.expect("create table");
    }
    db
}

async fn seed_bot(db: &dyn DbPlugin, env: &str, bot_id: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (env, bot_id, ownership_version) VALUES (?, ?, 1)",
        vec![DbValue::from(env), DbValue::from(bot_id)],
    ))
    .await
    .expect("seed bot");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) \
         VALUES (?, ?, ?, 'owner', 0, 'approved')",
        vec![
            DbValue::from(env),
            DbValue::from("human_owner"),
            DbValue::from(bot_id),
        ],
    ))
    .await
    .expect("seed owner");
}

/// Seed `n` approved manager edges with ids `human_%06d` (multi-row, 100/stmt).
async fn seed_managers(db: &dyn DbPlugin, env: &str, bot_id: &str, n: usize) {
    for base in (0..n).step_by(CHUNK) {
        let take = CHUNK.min(n - base);
        let mut sql = String::from(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) VALUES ",
        );
        let mut params = Vec::new();
        for i in 0..take {
            if i > 0 {
                sql.push(',');
            }
            sql.push_str("(?, ?, ?, 'manager', 0, 'approved')");
            params.push(DbValue::from(env));
            params.push(DbValue::from(format!("human_{:06}", base + i)));
            params.push(DbValue::from(bot_id));
        }
        db.execute(DbStatement::with_params(sql, params))
            .await
            .expect("seed managers chunk");
    }
}

async fn count_approved(db: &dyn DbPlugin, env: &str, bot_id: &str, kind: &str) -> i64 {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM edge_grants \
             WHERE to_id=? AND env=? AND grant_kind=? AND status='approved'",
            vec![
                DbValue::from(bot_id),
                DbValue::from(env),
                DbValue::from(kind),
            ],
        ))
        .await
        .expect("count query");
    rows[0].get_i64("c").expect("count").expect("value")
}

async fn count_audit(db: &dyn DbPlugin, mutation: &str) -> i64 {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes WHERE mutation_id=?",
            vec![DbValue::from(mutation)],
        ))
        .await
        .expect("audit count");
    rows[0].get_i64("c").expect("count").expect("value")
}

/// Peak RSS of this process (VmHWM from /proc/self/status), in KiB.
fn vm_hwm_kib() -> u64 {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("VmHWM:") {
            return rest.trim().trim_end_matches("kB").trim().parse().unwrap_or(0);
        }
    }
    0
}

fn next_nonce() -> u64 {
    static N: AtomicU64 = AtomicU64::new(1);
    N.fetch_add(1, Ordering::SeqCst)
}

fn input_ids(n: usize, prefix: &str) -> Vec<String> {
    (0..n).map(|i| format!("{prefix}{i:06}")).collect()
}

// ---------------------------------------------------------------------------
// Transaction sketch (steps 0.2/0.3): one sync round as DbPlugin steps.
//
// Pre-transaction reads (outside, paginated, parameter preparation only):
//   S = current approved manager ids (paginated, ORDER BY from_id)
//   R = pre-existing revoked rows among input\S (classifies restore vs insert)
// In-transaction guards (Query steps; a zero-row Query alone does NOT fail a
// transaction, so each guard row must be consumed by a later step via
// DbTransactionParam::query_result):
//   step 0      bot lock/version row (MySQL sketch: FOR UPDATE)
//   step 1      owner guard -> exactly one approved owner edge -> owner token
//   step 2      count guard -> approved manager count (= owner excluded) == |S|
//   steps 3..   per-chunk membership guard: each <=100-id chunk of S fully present
// Writes (ExecuteChecked exact affected rows; audit BEFORE revoke per chunk):
//   S\input: audit INSERT..SELECT (records prior state) then revoke UPDATE
//   input\S: audit multi-row VALUES then INSERT (new) / restore UPDATE
// Sink (always last, ExecuteChecked expected=1): conditional bot-row CAS that
// verifies the post-state manager count equals |input| AND consumes the
// count-guard row and every chunk-guard row via `? IS NOT NULL` bindings, so
// a guard result can never be silently ignored (zero-diff transactions have
// no other write step to consume it).
// ---------------------------------------------------------------------------

struct SyncPlan {
    steps: Vec<DbTransactionStep>,
    revoke_edges: u64,
    restore_edges: u64,
    insert_edges: u64,
    audit_rows: u64,
    chunk_guards: usize,
    total_steps: usize,
}

fn build_sync_plan(
    env: &str,
    bot_id: &str,
    s: &[String],
    input: &[String],
    restores: &[String],
    nonce: u64,
) -> SyncPlan {
    let mut steps: Vec<DbTransactionStep> = Vec::new();
    let mutation_id = format!("mut_{nonce:012}");
    let actor_id = "service::app_1";

    // Step 0: bot lock row.
    steps.push(DbTransactionStep::Query(DbStatement::with_params(
        "SELECT bot_id, env, ownership_version AS ov FROM bcs_bots WHERE env=? AND bot_id=?",
        vec![DbValue::from(env), DbValue::from(bot_id)],
    )));

    // Step 1: owner guard. One row iff exactly one approved owner edge exists;
    // missing or duplicated owner -> zero rows -> later bindings fail.
    steps.push(DbTransactionStep::Query(DbStatement::with_transaction_params(
        "SELECT t.owner_id AS owner_id, t.owner_n AS owner_n FROM \
           (SELECT MIN(from_id) AS owner_id, COUNT(*) AS owner_n FROM edge_grants \
             WHERE to_id=? AND env=? AND grant_kind='owner' AND status='approved') t \
         WHERE t.owner_n = 1",
        vec![
            DbTransactionParam::query_result(0, 0, "bot_id"),
            DbTransactionParam::Value(DbValue::from(env)),
        ],
    )));

    // Step 2: count guard. One row iff current count == |S|.
    steps.push(DbTransactionStep::Query(DbStatement::with_transaction_params(
        "SELECT f.c AS current_cnt FROM \
           (SELECT COUNT(*) AS c FROM edge_grants \
             WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0 \
               AND status='approved' AND from_id<>?) f \
         WHERE f.c = ?",
        vec![
            DbTransactionParam::query_result(0, 0, "bot_id"),
            DbTransactionParam::Value(DbValue::from(env)),
            DbTransactionParam::query_result(1, 0, "owner_id"),
            DbTransactionParam::Value(DbValue::from(s.len() as u64)),
        ],
    )));

    // Steps 3..: per-chunk membership guards over S (sorted chunks).
    let mut chunk_guard_step_ids = Vec::new();
    for (chunk_index, ids) in s.chunks(CHUNK).enumerate() {
        let step_index = 3 + chunk_index;
        let in_sql = vec!["?"; ids.len()].join(",");
        let sql = format!(
            "SELECT m.member_cnt AS ok FROM \
               (SELECT COUNT(*) AS member_cnt FROM edge_grants \
                 WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0 \
                   AND status='approved' AND from_id<>? AND from_id IN ({in_sql})) m \
             WHERE m.member_cnt = ?"
        );
        let mut params: Vec<DbTransactionParam> = vec![
            DbTransactionParam::query_result(0, 0, "bot_id"),
            DbTransactionParam::Value(DbValue::from(env)),
            DbTransactionParam::query_result(1, 0, "owner_id"),
        ];
        for id in ids {
            params.push(DbTransactionParam::Value(DbValue::from(id.as_str())));
        }
        params.push(DbTransactionParam::Value(DbValue::from(ids.len() as u64)));
        chunk_guard_step_ids.push(step_index);
        steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(sql, params),
        ));
    }

    // Diff: sorted stable set-difference (no diff -> no audit, sink only).
    let revokes: Vec<String> = s
        .iter()
        .filter(|id| !input.contains(id))
        .cloned()
        .collect();
    let adds: Vec<String> = input
        .iter()
        .filter(|id| !s.contains(id))
        .cloned()
        .collect();
    let restore_ids: Vec<String> = adds
        .iter()
        .filter(|id| restores.contains(*id))
        .cloned()
        .collect();
    let insert_ids: Vec<String> = adds
        .iter()
        .filter(|id| !restores.contains(*id))
        .cloned()
        .collect();

    let mut seq: u64 = 1;
    let mut revoke_edges = 0u64;
    let mut restore_edges = 0u64;
    let mut insert_edges = 0u64;
    let mut audit_rows = 0u64;
    let mut write_steps = 0usize;

    // Revoke chunks: audit INSERT..SELECT first (records prior state), then
    // conditional revoke UPDATE; both ExecuteChecked with exact counts. The
    // first chunk re-verifies the in-transaction manager count still equals
    // |S| inside its audit predicate, consuming the count-guard row (step 2).
    for (chunk_index, ids) in revokes.chunks(CHUNK).enumerate() {
        let k = ids.len() as u64;
        let in_sql = vec!["?"; ids.len()].join(",");
        let base = seq;
        seq += k;
        let count_recheck = if chunk_index == 0 {
            " AND (SELECT COUNT(*) FROM edge_grants WHERE to_id=? AND env=? \
               AND grant_kind='manager' AND grant_ref_id=0 AND status='approved' \
               AND from_id<>?) = ?"
        } else {
            ""
        };
        let mut params: Vec<DbTransactionParam> = vec![
            DbTransactionParam::Value(DbValue::from(env)),
            DbTransactionParam::Value(DbValue::from(bot_id)),
            DbTransactionParam::Value(DbValue::from(mutation_id.as_str())),
            DbTransactionParam::Value(DbValue::from(base)),
            DbTransactionParam::Value(DbValue::from(actor_id)),
            DbTransactionParam::Value(DbValue::from(bot_id)),
            DbTransactionParam::Value(DbValue::from(env)),
            DbTransactionParam::query_result(1, 0, "owner_id"),
        ];
        for id in ids {
            params.push(DbTransactionParam::Value(DbValue::from(id.as_str())));
        }
        if chunk_index == 0 {
            params.push(DbTransactionParam::Value(DbValue::from(bot_id)));
            params.push(DbTransactionParam::Value(DbValue::from(env)));
            params.push(DbTransactionParam::query_result(1, 0, "owner_id"));
            params.push(DbTransactionParam::query_result(2, 0, "current_cnt"));
        }
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                format!(
                    "INSERT INTO bot_authority_changes \
                       (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, \
                        subject_user_id, before_status, after_status) \
                     SELECT ?, ?, ?, ? + ROW_NUMBER() OVER (ORDER BY e.from_id) - 1, \
                            'manager_revoke', 'service', ?, e.from_id, 'approved', 'revoked' \
                     FROM edge_grants e \
                     WHERE e.to_id=? AND e.env=? AND e.grant_kind='manager' \
                       AND e.grant_ref_id=0 AND e.status='approved' \
                       AND e.from_id<>? AND e.from_id IN ({in_sql}){count_recheck}"
                ),
                params,
            ),
            expected_affected_rows: k,
        });
        write_steps += 1;
        let mut revoke_params: Vec<DbTransactionParam> = vec![
            DbTransactionParam::Value(DbValue::from(bot_id)),
            DbTransactionParam::Value(DbValue::from(env)),
        ];
        for id in ids {
            revoke_params.push(DbTransactionParam::Value(DbValue::from(id.as_str())));
        }
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                format!(
                    "UPDATE edge_grants SET status='revoked', gmt_modified=CURRENT_TIMESTAMP \
                     WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0 \
                       AND status='approved' AND from_id IN ({in_sql})"
                ),
                revoke_params,
            ),
            expected_affected_rows: k,
        });
        write_steps += 1;
        revoke_edges += k;
        audit_rows += k;
    }

    // Add chunks: inserts (new edges) and restores (revoked -> approved).
    for ids in insert_ids.chunks(CHUNK) {
        let k = ids.len() as u64;
        let mut edge_values = String::new();
        let mut edge_params: Vec<DbTransactionParam> = Vec::new();
        let mut audit_params: Vec<DbTransactionParam> = Vec::new();
        for (i, id) in ids.iter().enumerate() {
            if i > 0 {
                edge_values.push(',');
            }
            edge_values.push_str("(?, ?, ?, 'manager', 0, 'approved')");
            let id = id.as_str();
            edge_params.push(DbTransactionParam::Value(DbValue::from(env)));
            edge_params.push(DbTransactionParam::Value(DbValue::from(id)));
            edge_params.push(DbTransactionParam::Value(DbValue::from(bot_id)));
        }
        for (i, id) in ids.iter().enumerate() {
            // Push the 10 audit params for this row.
            audit_params.push(DbTransactionParam::Value(DbValue::from(env)));
            audit_params.push(DbTransactionParam::Value(DbValue::from(bot_id)));
            audit_params.push(DbTransactionParam::Value(DbValue::from(mutation_id.as_str())));
            audit_params.push(DbTransactionParam::Value(DbValue::from(seq + i as u64)));
            audit_params.push(DbTransactionParam::Value(DbValue::from("manager_grant")));
            audit_params.push(DbTransactionParam::Value(DbValue::from("service")));
            audit_params.push(DbTransactionParam::Value(DbValue::from(actor_id)));
            audit_params.push(DbTransactionParam::Value(DbValue::from(id.as_str())));
            audit_params.push(DbTransactionParam::Value(DbValue::Null));
            audit_params.push(DbTransactionParam::Value(DbValue::from("approved")));
        }
        seq += k;
        let audit_values: String = vec!["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"; ids.len()].join(",");
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                format!(
                    "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) \
                     VALUES {edge_values}"
                ),
                edge_params,
            ),
            expected_affected_rows: k,
        });
        write_steps += 1;
        // Sub-chunk the multi-row audit VALUES to stay below SQLite's
        // default 999 bound-variable limit (10 params per row).
        for params in audit_params.chunks(90 * 10) {
            let rows = params.len() / 10;
            let audit_values: String =
                vec!["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"; rows].join(",");
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_transaction_params(
                    format!(
                        "INSERT INTO bot_authority_changes \
                           (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, \
                            subject_user_id, before_status, after_status) \
                         VALUES {audit_values}"
                    ),
                    params.to_vec(),
                ),
                expected_affected_rows: rows as u64,
            });
            write_steps += 1;
        }
insert_edges += k;
        audit_rows += k;
    }
    for ids in restore_ids.chunks(CHUNK) {
        let k = ids.len() as u64;
        let in_sql = vec!["?"; ids.len()].join(",");
        let mut edge_params: Vec<DbTransactionParam> = vec![
            DbTransactionParam::Value(DbValue::from(bot_id)),
            DbTransactionParam::Value(DbValue::from(env)),
        ];
        let mut audit_params: Vec<DbTransactionParam> = Vec::new();
        for id in ids {
            edge_params.push(DbTransactionParam::Value(DbValue::from(id.as_str())));
        }
        for (i, id) in ids.iter().enumerate() {
            for value in [
                DbValue::from(env),
                DbValue::from(bot_id),
                DbValue::from(mutation_id.as_str()),
                DbValue::from(seq + i as u64),
                DbValue::from("manager_restore"),
                DbValue::from("service"),
                DbValue::from(actor_id),
                DbValue::from(id.as_str()),
                DbValue::from("revoked"),
                DbValue::from("approved"),
            ] {
                audit_params.push(DbTransactionParam::Value(value));
            }
        }
        seq += k;
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                format!(
                    "UPDATE edge_grants SET status='approved', gmt_modified=CURRENT_TIMESTAMP \
                     WHERE to_id=? AND env=? AND grant_kind='manager' AND grant_ref_id=0 \
                       AND status='revoked' AND from_id IN ({in_sql})"
                ),
                edge_params,
            ),
            expected_affected_rows: k,
        });
        write_steps += 1;
        let audit_values: String = vec!["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"; ids.len()].join(",");
        // Sub-chunk the multi-row audit VALUES to stay below SQLite's
        // default 999 bound-variable limit (10 params per row).
        for params in audit_params.chunks(90 * 10) {
            let rows = params.len() / 10;
            let audit_values: String =
                vec!["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"; rows].join(",");
            steps.push(DbTransactionStep::ExecuteChecked {
                statement: DbStatement::with_transaction_params(
                    format!(
                        "INSERT INTO bot_authority_changes \
                           (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, \
                            subject_user_id, before_status, after_status) \
                         VALUES {audit_values}"
                    ),
                    params.to_vec(),
                ),
                expected_affected_rows: rows as u64,
            });
            write_steps += 1;
        }
restore_edges += k;
        audit_rows += k;
    }

    // Sink (always last): conditional bot-row CAS verifying the post-state
    // manager count equals |input|, consuming the count-guard and every
    // chunk-guard row through `? IS NOT NULL` bindings.
    let mut sink_sql = String::from(
        "UPDATE bcs_bots SET gmt_modified = CURRENT_TIMESTAMP \
         WHERE env=? AND bot_id=? \
           AND (SELECT COUNT(*) FROM edge_grants WHERE to_id=? AND env=? \
                AND grant_kind='manager' AND grant_ref_id=0 AND status='approved' \
                AND from_id<>?) = ?",
    );
    let mut sink_params: Vec<DbTransactionParam> = vec![
        DbTransactionParam::Value(DbValue::from(env)),
        DbTransactionParam::Value(DbValue::from(bot_id)),
        DbTransactionParam::Value(DbValue::from(bot_id)),
        DbTransactionParam::Value(DbValue::from(env)),
        DbTransactionParam::query_result(1, 0, "owner_id"),
        DbTransactionParam::Value(DbValue::from(input.len() as u64)),
        DbTransactionParam::query_result(2, 0, "current_cnt"),
    ];
    sink_sql.push_str(" AND ? IS NOT NULL");
    for step_id in &chunk_guard_step_ids {
        sink_sql.push_str(" AND ? IS NOT NULL");
        sink_params.push(DbTransactionParam::query_result(*step_id, 0, "ok"));
    }
    steps.push(DbTransactionStep::ExecuteChecked {
        statement: DbStatement::with_transaction_params(sink_sql, sink_params),
        expected_affected_rows: 1,
    });

    SyncPlan {
        total_steps: steps.len(),
        chunk_guards: chunk_guard_step_ids.len(),
        revoke_edges,
        restore_edges,
        insert_edges,
        audit_rows,
        steps,
    }
}

// ---------------------------------------------------------------------------

struct SyncRun {
    result: Result<Vec<DbTransactionStepResult>, DbError>,
    pre_pages: usize,
    restore_pages: usize,
    steps: usize,
    chunk_guards: usize,
    expected_rows: u64,
    revoke_edges: u64,
    insert_edges: u64,
    restore_edges: u64,
    audit_rows: u64,
    pre_ms: u128,
    tx_ms: u128,
}

impl SyncRun {
    fn expect_ok(&self, what: &str) {
        if let Err(err) = &self.result {
            panic!("{what}: transaction failed: {err}");
        }
    }
    fn expect_err(&self, what: &str) -> &DbError {
        match &self.result {
            Err(err) => err,
            Ok(_) => panic!("{what}: expected transaction failure, got Ok"),
        }
    }
    fn summary(&self, what: &str) -> String {
        let state = match &self.result {
            Ok(_) => "Ok",
            Err(err) => &err.to_string()[..],
        };
        let mut text = String::new();
        let _ = writeln!(
            text,
            "{what}: result={state} pre_read_pages={} restore_pages={} total_steps={} \
             chunk_guards={} writes(rows: revoke={} insert={} restore={} audit={} sum={}) \
             expected_rows={} pre_ms={} tx_ms={}",
            self.pre_pages,
            self.restore_pages,
            self.steps,
            self.chunk_guards,
            self.revoke_edges,
            self.insert_edges,
            self.restore_edges,
            self.audit_rows,
            self.revoke_edges + self.insert_edges + self.restore_edges + self.audit_rows,
            self.expected_rows,
            self.pre_ms,
            self.tx_ms,
        );
        text
    }
}

async fn preread_managers(db: &dyn DbPlugin, env: &str, bot_id: &str) -> (Vec<String>, usize) {
    let mut out = Vec::new();
    let mut pages = 0usize;
    loop {
        let rows = db
            .query(DbStatement::with_params(
                "SELECT from_id FROM edge_grants WHERE to_id=? AND env=? \
                   AND grant_kind='manager' AND status='approved' \
                 ORDER BY from_id LIMIT 100 OFFSET ?",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(env),
                    DbValue::from(out.len() as u64),
                ],
            ))
            .await
            .expect("pre-read page");
        pages += 1;
        let page_len = rows.len();
        out.extend(
            rows.into_iter()
                .map(|r| r.get_string("from_id").expect("from_id").expect("value")),
        );
        if page_len < CHUNK {
            break;
        }
    }
    (out, pages)
}

async fn preread_restores(
    db: &dyn DbPlugin,
    env: &str,
    bot_id: &str,
    input: &[String],
) -> (Vec<String>, usize) {
    let mut out = Vec::new();
    let mut pages = 0usize;
    for ids in input.chunks(CHUNK) {
        pages += 1;
        let in_sql = vec!["?"; ids.len()].join(",");
        let mut params = vec![DbValue::from(bot_id), DbValue::from(env)];
        for id in ids {
            params.push(DbValue::from(id.as_str()));
        }
        let rows = db
            .query(DbStatement::with_params(
                format!(
                    "SELECT from_id FROM edge_grants WHERE to_id=? AND env=? \
                       AND grant_kind='manager' AND status='revoked' AND from_id IN ({in_sql})"
                ),
                params,
            ))
            .await
            .expect("restore pre-read");
        out.extend(
            rows.into_iter()
                .map(|r| r.get_string("from_id").expect("from_id").expect("value")),
        );
    }
    (out, pages)
}

/// The full sync flow under test: paginated pre-read (parameters only) +
/// one DbPlugin::transaction implementing the guarded diff sketch.
async fn run_sync(db: &Arc<dyn DbPlugin>, env: &str, bot_id: &str, input: &[String]) -> SyncRun {
    let t0 = Instant::now();
    let (s, pre_pages) = preread_managers(db.as_ref(), env, bot_id).await;
    let (restores, restore_pages) = preread_restores(db.as_ref(), env, bot_id, input).await;
    let pre_ms = t0.elapsed().as_millis();
    let plan = build_sync_plan(env, bot_id, &s, input, &restores, next_nonce());
    let sync_run = SyncRun {
        steps: plan.total_steps,
        chunk_guards: plan.chunk_guards,
        expected_rows: plan.revoke_edges + plan.restore_edges + plan.insert_edges + plan.audit_rows,
        revoke_edges: plan.revoke_edges,
        insert_edges: plan.insert_edges,
        restore_edges: plan.restore_edges,
        audit_rows: plan.audit_rows,
        pre_pages,
        restore_pages,
        pre_ms,
        tx_ms: 0,
        result: Ok(Vec::new()),
    };
    let t1 = Instant::now();
    let result = db.transaction(plan.steps).await;
    let tx_ms = t1.elapsed().as_millis();
    SyncRun {
        result,
        tx_ms,
        ..sync_run
    }
}

// ---------------------------------------------------------------------------
// 0.3 mechanism proofs
// ---------------------------------------------------------------------------

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s1_guard_token_missing_row_fails_whole_transaction() {
    let db = fresh_db().await;
    seed_bot(&*db, "dev", "bot1").await;
    seed_managers(&*db, "dev", "bot1", 5).await;
    let hwm_before = vm_hwm_kib();

    // (a) owner present: the guarded sync commits.
    let run = run_sync(&db, "dev", "bot1", &input_ids(3, "human_0000")).await;
    run.expect_ok("owner present");
    println!("{}", run.summary("s1a_guarded_sync_commits"));

    // (b) inject guard failure: delete the owner edge -> owner guard returns
    // zero rows -> downstream query_result bindings fail the whole transaction.
    db.execute(DbStatement::new("DELETE FROM edge_grants WHERE grant_kind='owner'"))
        .await
        .expect("drop owner");
    let run = run_sync(&db, "dev", "bot1", &input_ids(3, "human_0000")).await;
    let err = run.expect_err("owner missing");
    println!("s1b_owner_guard_failure: {err}");
    assert!(
        err.to_string().contains("references missing row"),
        "expected missing-row binding failure, got: {err}"
    );
    // s1a synced to 3 managers; the failed sync in s1b must have left
    // those 3 exactly untouched (no partial revoke).
    assert_eq!(
        count_approved(&*db, "dev", "bot1", "manager").await,
        3,
        "no manager may change when the owner guard fails"
    );

    // (c) plain zero-row Query does NOT fail a transaction (it continues), so
    // it must never be used as a guard: this benign transaction succeeds and
    // persists its write despite a zero-row SELECT.
    db.transaction(vec![
        DbTransactionStep::Query(DbStatement::with_params(
            "SELECT from_id FROM edge_grants WHERE to_id=? AND env=? AND grant_kind='owner'",
            vec![DbValue::from("bot1"), DbValue::from("dev")],
        )),
        DbTransactionStep::Execute(DbStatement::with_params(
            "INSERT INTO t0_marker (name) VALUES ('zero_row_query_continues')",
            vec![],
        )),
    ])
    .await
    .expect("zero-row query continues");
    let rows = db
        .query(DbStatement::new("SELECT COUNT(*) AS c FROM t0_marker"))
        .await
        .unwrap();
    assert_eq!(rows[0].get_i64("c").unwrap(), Some(1));
    println!("s1c_zero_row_query_continues: committed={}", rows[0].get_i64("c").unwrap().unwrap());
    let _ = hwm_before;
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s2_stop_on_no_rows_commits_prefix_never_rollback() {
    let db = fresh_db().await;
    // stop_on_no_rows is documented to COMMIT the executed prefix. Prove it:
    // the earlier INSERT stays committed when the later zero-row Execute ends
    // the transaction early, so it must not be used as a rollback mechanism.
    let result = db
        .transaction(vec![
            DbTransactionStep::Execute(DbStatement::with_params(
                "INSERT INTO t0_marker (name) VALUES ('prefix_committed')",
                vec![],
            )),
            DbTransactionStep::Execute(
                DbStatement::with_params(
                    "UPDATE t0_marker SET name=name WHERE name=?",
                    vec![DbValue::from("does_not_exist")],
                )
                .with_transaction_stop_on_no_rows(),
            ),
            DbTransactionStep::Execute(DbStatement::new("INSERT INTO t0_marker (name) VALUES ('never_reached')")),
        ])
        .await
        .expect("stop-on-no-rows commits prefix");
    assert_eq!(result.len(), 2, "only reached steps are returned");
    let rows = db
        .query(DbStatement::new("SELECT name FROM t0_marker"))
        .await
        .unwrap();
    let names: Vec<String> = rows
        .iter()
        .map(|r| r.get_string("name").unwrap().unwrap())
        .collect();
    assert!(names.contains(&"prefix_committed".to_string()));
    assert!(!names.contains(&"never_reached".to_string()));
    println!("s2_stop_on_no_rows: committed prefix rows = {names:?}");
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s3_audit_failure_and_count_mismatch_roll_back_everything() {
    let db = fresh_db().await;
    seed_bot(&*db, "dev", "bot1").await;
    seed_managers(&*db, "dev", "bot1", 300).await;

    // (a) audit failure injected between chunk writes: the plan uses a
    // fixed mutation id `mut_000000000042`; pre-seed one colliding row
    // at sequence 150 so the SECOND chunk's audit INSERT hits the
    // UNIQUE(env, mutation_id, sequence) violation after the first
    // chunk's audit+revoke already applied. All earlier writes must roll back.
    let (s, _) = preread_managers(&*db, "dev", "bot1").await;
    let plan = build_sync_plan("dev", "bot1", &s, &[], &[], 42);
    db.execute(DbStatement::with_params(
        "INSERT INTO bot_authority_changes (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, subject_user_id, before_status, after_status) \
         VALUES ('dev','bot1','mut_000000000042',150,'manager_revoke','service','injected','human_collide','approved','revoked')",
        vec![],
    ))
    .await
    .expect("seed collision row");
    let t1 = Instant::now();
    let result = db.transaction(plan.steps).await;
    let tx_ms = t1.elapsed().as_millis();
    let err = match result {
        Err(err) => err,
        Ok(_) => panic!("expected audit failure to fail the transaction"),
    };
    println!("s3a_audit_failure: err={err} tx_ms={tx_ms}");
    assert!(
        err.to_string().contains("UNIQUE constraint failed"),
        "expected unique violation, got: {err}"
    );
    assert_eq!(
        count_approved(&*db, "dev", "bot1", "manager").await,
        300,
        "all 300 managers must survive the rollback"
    );
    assert_eq!(count_audit(&*db, "mut_000000000042").await, 1, "no audit row leaked past rollback");

    // (b) ExecuteChecked count mismatch: one manager silently replaced by a
    // different identity (same count) -> the first revoke chunk must fail
    // with ConditionFailed and roll back everything.
    db.execute(DbStatement::with_params(
        "UPDATE edge_grants SET status='revoked' WHERE from_id='human_000042' AND env='dev' AND to_id='bot1'",
        vec![],
    ))
    .await
    .expect("revoke one manager");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) VALUES ('dev','human_904242','bot1','manager',0,'approved')",
        vec![],
    ))
    .await
    .expect("insert replacement");
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 300);
    let (s, _) = preread_managers(&*db, "dev", "bot1").await;
    assert_eq!(s.len(), 300);
    // Prove the chunk-level mechanism itself: revoke one S member and insert
    // a different approved one. Total count stays 300 so the count guard
    // passes, but that member's chunk finds 99 of its 100 S members
    // -> the chunk ExecuteChecked must fail the whole transaction.
    db.execute(DbStatement::with_params(
        "UPDATE edge_grants SET status='revoked' WHERE from_id=? AND env='dev' AND to_id='bot1'",
        vec![DbValue::from(s[299].as_str())],
    ))
    .await
    .expect("revoke one member");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) \
         VALUES ('dev','human_929901','bot1','manager',0,'approved')",
        vec![],
    ))
    .await
    .expect("insert another replacement");
    let plan = build_sync_plan("dev", "bot1", &s, &[], &[], next_nonce());
    let result = db.transaction(plan.steps).await;
    let err = match result {
        Err(err) => err,
        Ok(_) => panic!("expected ConditionFailed for 99-of-100 chunk"),
    };
    println!("s3b_count_mismatch: err={err}");
    assert!(matches!(err, DbError::ConditionFailed { .. }), "got: {err}");
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s4_same_count_replacement_rolls_back_and_retry_succeeds() {
    let db = fresh_db().await;
    seed_bot(&*db, "dev", "bot1").await;
    seed_managers(&*db, "dev", "bot1", 100).await;
    let (s, _) = preread_managers(&*db, "dev", "bot1").await;
    assert_eq!(s.len(), 100);

    // Concurrent change between pre-read and transaction: same-count but
    // different-identity replacement (human_000009 -> human_909009).
    db.execute(DbStatement::with_params(
        "UPDATE edge_grants SET status='revoked' WHERE from_id='human_000009' AND env='dev' AND to_id='bot1'",
        vec![],
    ))
    .await
    .expect("revoke replaced member");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) VALUES ('dev','human_909009','bot1','manager',0,'approved')",
        vec![],
    ))
    .await
    .expect("insert replacement");

    // Stale sync-to-empty computed from the pre-read S: the count guard still
    // passes (count==100) but chunk 1 membership finds 99 of the S chunk ->
    // the write must fail and roll back with zero persisted changes.
    let plan = build_sync_plan("dev", "bot1", &s, &[], &[], next_nonce());
    let result = db.transaction(plan.steps).await;
    let err = match result {
        Err(err) => err,
        Ok(_) => panic!("stale same-count replacement must fail"),
    };
    println!("s4_stale_replacement: err={err}");
    assert!(matches!(err, DbError::ConditionFailed { .. }), "got: {err}");
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 100);
    assert_eq!(count_approved(&*db, "dev", "bot1", "owner").await, 1);

    // Retry with a fresh pre-read succeeds and covers exactly the real set.
    let run = run_sync(&db, "dev", "bot1", &[]).await;
    run.expect_ok("fresh pre-read sync-to-empty");
    println!("{}", run.summary("s4_retry_sync_to_empty"));
    assert_eq!(
        count_approved(&*db, "dev", "bot1", "manager").await,
        0,
        "including the replacement, not just the stale S members"
    );
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s5_zero_diff_sync_writes_zero_audit_and_detects_stale_set() {
    let db = fresh_db().await;
    seed_bot(&*db, "dev", "bot1").await;
    seed_managers(&*db, "dev", "bot1", 60).await;

    // Idempotent repeat sync (S == input): zero diff, zero audit rows, but the
    // guards and sink still run and commit within the same wall-clock second.
    let (s, _) = preread_managers(&*db, "dev", "bot1").await;
    let run = run_sync_with_s(&db, "dev", "bot1", &s, s.clone()).await;
    run.expect_ok("zero-diff sync");
    println!("{}", run.summary("s5a_zero_diff"));
    assert_eq!(run.audit_rows + run.revoke_edges + run.insert_edges + run.restore_edges, 0);
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 60);

    // Repeat immediately (same gmt_modified second) — the sink UPDATE still
    // affects exactly one row on SQLite (row rewrite semantics).
    let run = run_sync(&db, "dev", "bot1", &s).await;
    run.expect_ok("repeat zero-diff sync");
    println!("s5a_repeat_same_second: tx_ms={}", run.tx_ms);

    // Stale zero-diff: managers changed after the pre-read (same count,
    // different membership). The chunk/total guards no longer hold; the sink
    // binding must fail the transaction instead of silently reporting success.
    db.execute(DbStatement::with_params(
        "UPDATE edge_grants SET status='revoked' WHERE from_id='human_000010' AND env='dev' AND to_id='bot1'",
        vec![],
    ))
    .await
    .expect("revoke one");
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) VALUES ('dev','human_101010','bot1','manager',0,'approved')",
        vec![],
    ))
    .await
    .expect("add replacement");
    let plan_run = run_sync_with_s(&db, "dev", "bot1", &s, s.clone()).await;
    let err = plan_run.expect_err("stale zero-diff");
    println!("s5b_stale_zero_diff: err={err}");
    assert!(
        matches!(err, DbError::ConditionFailed { .. } | DbError::InvalidInput(_)),
        "got: {err}"
    );
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 60);
}

/// Like run_sync, but the caller supplies S directly (to build a stale plan).
async fn run_sync_with_s(
    db: &Arc<dyn DbPlugin>,
    env: &str,
    bot_id: &str,
    s: &[String],
    input: Vec<String>,
) -> SyncRun {
    let (restores, restore_pages) = preread_restores(db.as_ref(), env, bot_id, &input).await;
    // input == S here, so no restore pre-read is strictly needed; keep it for
    // symmetric measurement.
    let _ = restore_pages;
    let plan = build_sync_plan(env, bot_id, s, &input, &restores, next_nonce());
    let sync_run = SyncRun {
        steps: plan.total_steps,
        chunk_guards: plan.chunk_guards,
        expected_rows: plan.revoke_edges + plan.restore_edges + plan.insert_edges + plan.audit_rows,
        revoke_edges: plan.revoke_edges,
        insert_edges: plan.insert_edges,
        restore_edges: plan.restore_edges,
        audit_rows: plan.audit_rows,
        pre_pages: 0,
        restore_pages,
        pre_ms: 0,
        tx_ms: 0,
        result: Ok(Vec::new()),
    };
    let t1 = Instant::now();
    let result = db.transaction(plan.steps).await;
    let tx_ms = t1.elapsed().as_millis();
    SyncRun {
        result,
        tx_ms,
        ..sync_run
    }
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s6_empty_sync_requires_count_zero_and_audits_nothing() {
    let db = fresh_db().await;
    seed_bot(&*db, "dev", "bot1").await;

    // Truly empty sync (no managers at all, S=empty): count guard asserts
    // count==0 and the sink consumes it.
    let plan = build_sync_plan("dev", "bot1", &[], &[], &[], next_nonce());
    db.transaction(plan.steps)
        .await
        .expect("truly empty sync commits");
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 0);
    seed_managers(&*db, "dev", "bot1", 7).await;

    // Empty input over a non-empty S, but with a stale pre-read claiming S is
    // empty: the count guard must fail the transaction (cannot silently treat
    // existing managers as absent).
    let plan = build_sync_plan("dev", "bot1", &[], &[], &[], next_nonce());
    let result = db.transaction(plan.steps).await;
    let err = match result {
        Err(err) => err,
        Ok(_) => panic!("empty-S guard must verify count=0"),
    };
    println!("s6_stale_empty_sync: err={err}");
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 7);

    // Honest empty sync (pre-read saw 7, input empty): clears all 7, audits 7.
    let run = run_sync(&db, "dev", "bot1", &[]).await;
    run.expect_ok("honest sync to empty");
    println!("{}", run.summary("s6_sync_7_to_empty"));
    assert_eq!(count_approved(&*db, "dev", "bot1", "manager").await, 0);
}

// ---------------------------------------------------------------------------
// 0.4 capacity proofs (real SQLite plugin)
// ---------------------------------------------------------------------------

const SCRATCH_DIR: &str = "/tmp/t0_bot_authority_scratch";

async fn capacity_case(db: &Arc<dyn DbPlugin>, stock: usize, input: usize, name: &str) {
    seed_bot(db.as_ref(), "dev", "bot_cap").await;
    seed_managers(db.as_ref(), "dev", "bot_cap", stock).await;

    // Disjoint input ids get no restore pre-read hits; use a distinct prefix.
    let target = input_ids(input, "x_new_");
    let hwm_before = vm_hwm_kib();
    let run = run_sync(db, "dev", "bot_cap", &target).await;
    let hwm_after = vm_hwm_kib();

    run.expect_ok(name);
    let print = run.summary(name);
    println!(
        "{print}  vm_hwm_before_kb={} vm_hwm_after_kb={} hwm_delta_kb={}",
        hwm_before,
        hwm_after,
        hwm_after.saturating_sub(hwm_before)
    );
    let managers = count_approved(db.as_ref(), "dev", "bot_cap", "manager").await;
    let audit = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes WHERE bot_id=? AND env=?",
            vec![DbValue::from("bot_cap"), DbValue::from("dev")],
        ))
        .await
        .unwrap();
    println!(
        "  post_state: approved_managers={managers} (want {input}) audit_rows={} (want {})",
        audit[0].get_i64("c").unwrap().unwrap(),
        run.audit_rows
    );
    assert_eq!(managers, input as i64);
    assert_eq!(audit[0].get_i64("c").unwrap().unwrap(), run.audit_rows as i64);
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s7_capacity_1001_to_empty() {
    let db = fresh_db().await;
    capacity_case(&db, 1_001, 0, "s7_capacity_1001_to_empty").await;
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s8_capacity_10000_to_empty() {
    let db = fresh_db().await;
    capacity_case(&db, 10_000, 0, "s8_capacity_10000_to_empty").await;
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s9_capacity_10000_to_1000_disjoint() {
    let db = fresh_db().await;
    capacity_case(&db, 10_000, 1_000, "s9_capacity_10000_to_1000_disjoint").await;
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s9b_capacity_10001_to_empty_guard_chain() {
    let db = fresh_db().await;
    // 100 chunk guards + 101 revoke chunks + owner/count guards + sink.
    capacity_case(&db, 10_001, 0, "s9b_capacity_10001_to_empty").await;
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s12_sql_variable_limit_probe() {
    let db = fresh_db().await;
    db.execute(DbStatement::new(
        "CREATE TABLE t0_vars (c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT, c5 TEXT, \
         c6 TEXT, c7 TEXT, c8 TEXT, c9 TEXT, c10 TEXT)",
    ))
    .await
    .expect("create t0_vars");

    // One audit row binds 10 values in the VALUES list; build the same
    // shape here to probe SQLite's default bound-variable limit.
    let build = |rows: usize| {
        let values = vec!["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"; rows].join(",");
        let mut params = Vec::new();
        for r in 0..rows {
            for c in 0..10 {
                params.push(DbValue::from(format!("v{r}_{c}")));
            }
        }
        DbStatement::with_params(format!("INSERT INTO t0_vars VALUES {values}"), params)
    };

    // 100 rows x 10 params = 1,000 bound values > 999 -> too many SQL variables.
    let over = db.execute(build(100)).await;
    let over_desc = match &over {
        Err(err) => err.to_string(),
        Ok(_) => "unexpected Ok".to_string(),
    };
    println!("s12_probe_100_rows_1000_params: {over_desc}");
    assert!(
        matches!(&over, Err(DbError::Backend(msg)) if msg.contains("too many SQL variables")),
        "expected 'too many SQL variables', got {over_desc}"
    );

    // 99 rows x 10 params = 990 <= 999 succeeds.
    db.execute(build(99)).await.expect("99 rows succeed");
    let rows = db
        .query(DbStatement::new("SELECT COUNT(*) AS c FROM t0_vars"))
        .await
        .unwrap();
    assert_eq!(rows[0].get_i64("c").unwrap(), Some(99));
    println!("s12_probe_99_rows_990_params: ok rows=99");
}

// ---------------------------------------------------------------------------
// 0.5 lock waits, busy timeout, and cancellation semantics
// ---------------------------------------------------------------------------

fn strip_newline(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).trim().to_string()
}

fn spawn_lock_holder(db_path: &str, hold_secs: u64) -> std::process::Child {
    let script = format!(
        "(printf \"BEGIN IMMEDIATE;\\nINSERT INTO t0_marker (name) VALUES ('lock_holder_{hold_secs}');\\n\"; \
         sleep {hold_secs}; \
         printf \"COMMIT;\\n\") | sqlite3 {db_path}"
    );
    Command::new("sh")
        .arg("-c")
        .arg(script)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .expect("spawn sqlite3 lock holder")
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s10_lock_waits_and_busy_timeout_5000() {
    std::fs::create_dir_all(SCRATCH_DIR).unwrap();
    let path = format!("{SCRATCH_DIR}/s10.db");
    let db = fresh_file_db(Path::new(&path)).await;
    seed_bot(&*db, "dev", "bot1").await;
    seed_managers(&*db, "dev", "bot1", 3_000).await;

    // (a) two competing big transactions: one must wait on the writer
    // lock held by BEGIN IMMEDIATE. Durations are reported; the
    // waiting one is inflated by the other's run time.
    let db2 = db.clone();
    let t0 = Instant::now();
    let a = tokio::spawn(async move { run_sync(&db2, "dev", "bot1", &[]).await });
    let db3 = db.clone();
    let b = tokio::spawn(async move { run_sync(&db3, "dev", "bot1", &[]).await });
    // Both target the same set; exactly one should win and the other
    // fail its guards (set already empty).
    let ra = a.await.expect("task a");
    let rb = b.await.expect("task b");
    println!("s10a_contending_durations: tx_a_ms={} tx_b_ms={}", ra.tx_ms, rb.tx_ms);
    match (&ra.result, &rb.result) {
        (Ok(_), Err(_)) | (Err(_), Ok(_)) => {
            println!("s10a: one committed, the other failed its guards:\n  a_err={:?}\n  b_err={:?}", ra.result.err(), rb.result.err());
        }
        (Ok(_), Ok(_)) => println!("s10a: both committed (serial execution, second saw empty set as no-diff)"),
        (Err(ea), Err(eb)) => panic!("both failed unexpectedly: {ea} | {eb}"),
    }
    println!("s10a wall_time_ms: {}", t0.elapsed().as_millis());

    // (b) holder within busy_timeout (4s): transaction waits then commits.
    let path_b = format!("{SCRATCH_DIR}/s10b.db");
    let db_b = fresh_file_db(Path::new(&path_b)).await;
    seed_bot(&*db_b, "dev", "bot1").await;
    seed_managers(&*db_b, "dev", "bot1", 50).await;
    let mut holder = spawn_lock_holder(&path_b, 4);
    tokio::time::sleep(Duration::from_millis(800)).await;
    let t0 = Instant::now();
    let result = db_b
        .transaction(vec![DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_bots SET gmt_modified = CURRENT_TIMESTAMP WHERE bot_id=? AND env=?",
            vec![DbValue::from("bot1"), DbValue::from("dev")],
        ))])
        .await;
    let waited = t0.elapsed();
    println!("s10b_wait_within_timeout: waited={:?} result={:?}", waited, result.is_ok());
    result.expect("should commit after <=5s wait");
    assert!(
        waited >= Duration::from_millis(3_000) && waited <= Duration::from_millis(5_500),
        "expected a ~4s lock wait, got {waited:?}"
    );
    let out = holder.wait().unwrap();
    println!("s10b holder exit: {out}");

    // (c) holder beyond busy_timeout (8s): transaction fails after
    // ~5s with 'database is locked'; the failed transaction made no
    // changes (sqlite BEGIN IMMEDIATE never acquired the write lock).
    let path_c = format!("{SCRATCH_DIR}/s10c.db");
    let db_c = fresh_file_db(Path::new(&path_c)).await;
    seed_bot(&*db_c, "dev", "bot1").await;
    seed_managers(&*db_c, "dev", "bot1", 50).await;
    let mut holder = spawn_lock_holder(&path_c, 8);
    tokio::time::sleep(Duration::from_millis(800)).await;
    let t0 = Instant::now();
    let result = db_c
        .transaction(vec![DbTransactionStep::Execute(DbStatement::with_params(
            "UPDATE bcs_bots SET gmt_modified = CURRENT_TIMESTAMP WHERE bot_id=? AND env=?",
            vec![DbValue::from("bot1"), DbValue::from("dev")],
        ))])
        .await;
    let waited = t0.elapsed();
    let err = result.expect_err("should fail on busy timeout");
    println!("s10c_busy_timeout: waited={waited:?} err={err}");
    let _ = holder.wait();
    assert!(
        waited >= Duration::from_millis(4_800) && waited < Duration::from_millis(7_000),
        "expected failure at ~5s busy timeout, got {waited:?}"
    );
    assert!(err.to_string().contains("locked"), "got: {err}");
}

#[tokio::test]
#[ignore = "Task 0 scratch harness; deleted before commit"]
async fn s11_outer_timeout_does_not_rollback() {
    std::fs::create_dir_all(SCRATCH_DIR).unwrap();
    let path = format!("{SCRATCH_DIR}/s11.db");
    let db = fresh_file_db(Path::new(&path)).await;
    seed_bot(&*db, "dev", "bot1").await;
    // 10,000 workload: the transaction runs ~700ms, so the abort at ~1ms
    // is guaranteed to hit WHILE the blocking transaction is mid-flight.
    seed_managers(&*db, "dev", "bot1", 10_000).await;

    // Abort the transaction future ~1ms after spawn ("outer Tokio
    // timeout") while the blocking transaction is still running on SQLite.
    // The blocking transaction keeps running to completion and COMMITS.
    // This proves an outer timeout is not rollback evidence.
    let db2 = db.clone();
    let (s, _) = preread_managers(&*db, "dev", "bot1").await;
    let plan = build_sync_plan("dev", "bot1", &s, &[], &[], next_nonce());
    let task = tokio::spawn(async move { db2.transaction(plan.steps).await });
    tokio::time::sleep(Duration::from_millis(1)).await;
    let _ = task.abort();
    let _ = task.await.is_err();

    // Poll until the abandoned transaction's writes appear.
    let t0 = Instant::now();
    let final_count = loop {
        let c = count_approved(&*db, "dev", "bot1", "manager").await;
        if c == 0 || t0.elapsed() > Duration::from_secs(30) {
            break c;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    };
    let elapsed = t0.elapsed();
    println!(
        "s11_aborted_tx_kept_running: stock=10000 abort_after=1ms final_managers={final_count} \
         after={elapsed:?} committed_despite_abort={} (start=10000)",
        final_count == 0
    );
    let audit = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bot_authority_changes",
            vec![],
        ))
        .await
        .unwrap();
    println!(
        "s11 audit rows committed despite abort: {}",
        audit[0].get_i64("c").unwrap().unwrap()
    );
    assert_eq!(final_count, 0, "the aborted transaction must still have committed");
    assert_eq!(audit[0].get_i64("c").unwrap(), Some(10_000));
}
```

---

## 附录 C 实际运行记录（评审修复后，全文逐字）

以下 13 个场景为最后一次串行运行（`--test-threads=1`）的逐字输出，未
增删改任何行；`test ... ... ok` 行中夹印的 println 来自测试内部
（libtest 的交错打印）。

```
running 13 tests
test s10_lock_waits_and_busy_timeout_5000 ... s10a_contending_durations: tx_a_ms=89 tx_b_ms=107
s10a: one committed, the other failed its guards:
  a_err=None
  b_err=Some(InvalidInput("transaction parameter 111 references missing row 0 from step 2"))
s10a wall_time_ms: 165
s10b_wait_within_timeout: waited=3.23352763s result=true
s10b holder exit: exit status: 0
s10c_busy_timeout: waited=5.005162426s err=database backend error: begin sqlite transaction: database is locked
ok
test s11_outer_timeout_does_not_rollback ... s11_aborted_tx_kept_running: stock=10000 abort_after=1ms final_managers=0 after=732.342777ms committed_despite_abort=true (start=10000)
s11 audit rows committed despite abort: 10000
ok
test s12_sql_variable_limit_probe ... s12_probe_100_rows_1000_params: database backend error: execute sqlite statement: too many SQL variables
s12_probe_99_rows_990_params: ok rows=99
ok
test s1_guard_token_missing_row_fails_whole_transaction ... s1a_guarded_sync_commits: result=Ok pre_read_pages=1 restore_pages=1 total_steps=9 chunk_guards=1 writes(rows: revoke=5 insert=3 restore=0 audit=8 sum=16) expected_rows=16 pre_ms=0 tx_ms=0

s1b_owner_guard_failure: invalid database input: transaction parameter 2 references missing row 0 from step 1
s1c_zero_row_query_continues: committed=1
ok
test s2_stop_on_no_rows_commits_prefix_never_rollback ... s2_stop_on_no_rows: committed prefix rows = ["prefix_committed"]
ok
test s3_audit_failure_and_count_mismatch_roll_back_everything ... s3a_audit_failure: err=database backend error: execute sqlite statement: UNIQUE constraint failed: bot_authority_changes.env, bot_authority_changes.mutation_id, bot_authority_changes.sequence tx_ms=2
s3b_count_mismatch: err=transaction condition failed: expected 100 affected rows, got 99
ok
test s4_same_count_replacement_rolls_back_and_retry_succeeds ... s4_stale_replacement: err=transaction condition failed: expected 100 affected rows, got 99
s4_retry_sync_to_empty: result=Ok pre_read_pages=2 restore_pages=0 total_steps=7 chunk_guards=1 writes(rows: revoke=100 insert=0 restore=0 audit=100 sum=200) expected_rows=200 pre_ms=0 tx_ms=1

ok
test s5_zero_diff_sync_writes_zero_audit_and_detects_stale_set ... s5a_zero_diff: result=Ok pre_read_pages=0 restore_pages=1 total_steps=5 chunk_guards=1 writes(rows: revoke=0 insert=0 restore=0 audit=0 sum=0) expected_rows=0 pre_ms=0 tx_ms=0

s5a_repeat_same_second: tx_ms=0
s5b_stale_zero_diff: err=invalid database input: transaction parameter 7 references missing row 0 from step 3
ok
test s6_empty_sync_requires_count_zero_and_audits_nothing ... s6_stale_empty_sync: err=invalid database input: transaction parameter 6 references missing row 0 from step 2
s6_sync_7_to_empty: result=Ok pre_read_pages=1 restore_pages=0 total_steps=7 chunk_guards=1 writes(rows: revoke=7 insert=0 restore=0 audit=7 sum=14) expected_rows=14 pre_ms=0 tx_ms=0

ok
test s7_capacity_1001_to_empty ... s7_capacity_1001_to_empty: result=Ok pre_read_pages=11 restore_pages=0 total_steps=37 chunk_guards=11 writes(rows: revoke=1001 insert=0 restore=0 audit=1001 sum=2002) expected_rows=2002 pre_ms=6 tx_ms=18
  vm_hwm_before_kb=11152 vm_hwm_after_kb=11152 hwm_delta_kb=0
  post_state: approved_managers=0 (want 0) audit_rows=1001 (want 1001)
ok
test s8_capacity_10000_to_empty ... s8_capacity_10000_to_empty: result=Ok pre_read_pages=101 restore_pages=0 total_steps=304 chunk_guards=100 writes(rows: revoke=10000 insert=0 restore=0 audit=10000 sum=20000) expected_rows=20000 pre_ms=424 tx_ms=701
  vm_hwm_before_kb=11528 vm_hwm_after_kb=15272 hwm_delta_kb=3744
  post_state: approved_managers=0 (want 0) audit_rows=10000 (want 10000)
ok
test s9_capacity_10000_to_1000_disjoint ... s9_capacity_10000_to_1000_disjoint: result=Ok pre_read_pages=101 restore_pages=10 total_steps=334 chunk_guards=100 writes(rows: revoke=10000 insert=1000 restore=0 audit=11000 sum=22000) expected_rows=22000 pre_ms=427 tx_ms=713
  vm_hwm_before_kb=15548 vm_hwm_after_kb=17224 hwm_delta_kb=1676
  post_state: approved_managers=1000 (want 1000) audit_rows=11000 (want 11000)
ok
test s9b_capacity_10001_to_empty_guard_chain ... s9b_capacity_10001_to_empty: result=Ok pre_read_pages=101 restore_pages=0 total_steps=307 chunk_guards=101 writes(rows: revoke=10001 insert=0 restore=0 audit=10001 sum=20002) expected_rows=20002 pre_ms=399 tx_ms=697
  vm_hwm_before_kb=17224 vm_hwm_after_kb=17224 hwm_delta_kb=0
  post_state: approved_managers=0 (want 0) audit_rows=10001 (want 10001)
ok

test result: ok. 13 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 17.53s
```
