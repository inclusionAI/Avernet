# Bot owner/manager 权限 — 全链路验收、成本与发布证据（Task 20，2026-10-09）

验收执行日：2026-10-09。分支 `feat/bot-owner-manager-permissions`，基线
`af160ec510`（Task 19 完成后），本任务净新增（见 §8）：
`crates/bootstrap/bcs/tests/e2e_bot_authority.rs`（新增）、
`crates/bootstrap/bcs/tests/e2e_ownership_transfer.rs`（新增）、
`scripts/e2e-test/bot_authority.sh`（新增），以及 5 个脚本/pytest/front-end 修复文件。

本文档自含：按用户 squash 流程，plan/validation 文档会被排除在 squash 之外，
因此这里逐项记录命令、真实结果、未验证项与原因，不引用“跑过”而无出处的陈述。

事实源：

- spec：`docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md`（§17 预算、§18 验收）。
- 计划与 RED/GREEN 台账：`.superpowers/sdd/2026-10-08-bot-owner-manager-implementation/progress.md`。
- 本文引用的所有数字都来自 2026-10-09 在本环境的实际执行或既有测试源码中的断言（逐处给出文件/行为号）。

---

## 1. 已执行门禁（逐项：命令 + 真实结果）

### 1.1 新增 E2E（RED→GREEN）

创建 `crates/bootstrap/bcs/tests/e2e_bot_authority.rs` /
`e2e_ownership_transfer.rs`。fixture 走真实生产装配：`BcsServer`（memory lane，
全量 v1 router + WS registry）：HTTP/WS 全部真实请求，无 domain 直调。

执行命令：

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs \
  --test e2e_bot_authority --test e2e_ownership_transfer
```

真实结果（2026-10-09，本机 `/shared/cargo/target`）：

```text
e2e_bot_authority:      test result: ok. 1 passed; 0 failed; finished in 1.4s
e2e_ownership_transfer: test result: ok. 1 passed; 0 failed; finished in 11.1s
```

RED 阶段证据（迭代中真实出现的失败，全部为 fixture/合同字段问题后被修正）：

1. `data.group.id` 取键失败（创建成功的响应里键是 `data.group_id`，flatten
   结构）——baseline 断言照抄 wiring 测试得来，修正后首个“真语义”断言通过。
2. 文件 content 上传 400 `content_length 22 != prepared size 16`（size 必须与
   prepare 声明一致）。
3. team sync 401：`Authorization: Bearer` 凭证误放在 `x-avernet-principal` header —— slice 挂在 principal 中间件之外，凭证必须走 Bearer（固定 401 形态）。
4. 第二笔转交 403 断言初版用了自转交目标（400 invalid_request "the owner
   cannot transfer a Bot to themselves"），修正为第三 Human 目标后语义命中。

GREEN 中覆盖的 Step-1 RED 变量组（均为本 e2e 文件内真实 HTTP 断言）：

```rust
// e2e_ownership_transfer.rs —— 传输故事
a_mine_after_transfer       ["access_relation"] == "manager"   // former owner 残留 manager 边
b_mine_after_transfer       ["access_relation"] == "owner"
former_owner_after_last_source_revoke_status == 403           // v1 PATCH 控制面写
pending_rows_for_bot       == 0   // 双方 status=pending 收发件箱 total=0 + 回执 accepted
active_owner_rows_for_bot  == 1   // 严格唯一 owner 读（0/多行时该读会结构性失败）返回 owner=B 且 version+1
runtime_friend_edges_after == runtime_friend_edges_before     // friend 快照逐字节相等
// e2e_bot_authority.rs —— 授权故事
mine 双向标签（A: X=owner/Y=manager，B: X=manager/Y=owner）+ legacy /bots/my 同标签
无 friend 边下 Human sponsorship 的私有 Group(X,Y) 创建成功
team sync/move/POST/DELETE repair 全部经真实凭证命中（C 的 team 来源边随快照增删）
session/file：owner 与 manager 混合 persona 均可 prepare/upload/complete/download（内容逐字节相等）
WS：owner/manager 双连接经装配的 enqueue/dequeue 授权服务投递受保护帧
```

### 1.2 聚焦 crate 测试（Step 3 -p 列表）

```bash
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-service-api -p bcs-edge-permission -p bcs-edge-permission-store \
  -p bcs-bot-store -p bcs-bot -p bcs-app-bot -p bcs-app-group -p bcs-app-session \
  -p bcs-app-invitation -p bcs-app-register -p bcs-group -p bcs-session \
  -p bcs-session-store -p bcs-session-file -p bcs-session-file-store \
  -p bcs-group-store -p bcs-message-store -p bcs-chat-run-store \
  -p bcs-api-http -p bcs-http -p bcs-ws -p bcs-jwt -p bcs-message-flow
```

真实结果：exit=0；`test result: ok` 256 段，无 FAILED，231 个 test binary。

### 1.3 架构门禁 arch-check.sh

```bash
(cd apps/bcs && bash scripts/ci/arch-check.sh)
```

真实结果（本环境）：**exit=5（FAIL）**，Total 15 / Pass 6 / Skip 4 / Fail 5，
Score 50/100。FAIL 项：`DEP-1~8`（bcs-service-api→bcs-config-api/bcs-storage-api
等依赖不在允许列表，例如 bcs-protocol→bcs-domain、
bcs-service-api→bcs-config-api/bcs-storage-api 均被 DEP-1 逐条点名），
`LINT-1`（CONTEXT.md §2/§6.1 处的既有 `use`
形态），`LINT-4`（`*Service/*Port` 命名规则，含 `BotAuthorityHook`、
`SessionFileService` 实现等 8 条），`TEST-1`，`R25.1/R25.2/R25.3`（一批
conformance 登记/cargo test 发现）。SKIP：BASELINE（非 PR 环境无法解析
origin/refactor_arch_bcs）、DEP-8（未安装 cargo-machete）等。

归属判断（诚实记录，不掩盖不“顺手修”）：本任务 diff 未触碰任何被 DEP/LINT/R25
扫描的对象（详见 §8 文件清单；其中 Rust 生产 source 为 2 个新测试文件，位于
`crates/bootstrap/bcs/tests/`，无 Cargo.toml、无 service-api src 改动）。因此
这些 FAIL 与 branch 基线一致，均为分支前况：R25.1/R25.2 的差量已在 Task 18 评审
中被裁决（82→76 / 88→85，恰为本计划引入的 6 个 harness/driver 条目，见 progress.md
Task 18 行）。本任务不声称 arch-check 通过。

### 1.4 OpenAPI 验证器（两个入口）

```bash
python3 apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1
python3 apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1 \
  --entrypoint internal.yaml --path-prefix /api/v1/collaboration/
```

真实结果：

```text
公开入口：    82 operations validated for openapi.yaml   exit=0
internal 入口：26 operations validated for internal.yaml exit=0
```

internal validator 未放宽为宽泛 `/api/v1/`；组合了精确的 team-manager 路径登记。

### 1.5 全工作区测试

```bash
cargo test --manifest-path apps/bcs/Cargo.toml --workspace --no-fail-fast
```

首跑（默认 fast-stop）在 `bcs-collaboration-store --test mysql_store` 处中止：
98 passed / 2 failed。--no-fail-fast 全量补跑（真实结果，2026-10-09）：

```text
574 个 test binary 全部执行：test result 573 段 ok + 1 段 FAILED；
总计 6534 passed；failed 2；ignored 69；退出码 101（仅由下述 2 个前况失败造成）
```

即除 §2.5 的 2 个既有失败外，全工作区无任何其他失败段。

已知前况（非本计划回归）：`bcs-collaboration-store` 2 个 sqlite 失败
（`failure_contract::sqlite_failure_action_migration_preserves_legacy_rows_and_replays`：
`near "DROP": syntax error`；`history_contract::sqlite_history_acceptance_and_terminal_replay_contract`：
`too many SQL variables`）——progress.md Task 6 环境注记已在计划开始时以 stash
核对（ledger-verified），git log 显示本计划 20 个任务未触碰该 crate。clippy 的
bcs-domain deny-lint 墙同为前况（本轮未跑 clippy，见 §2）。

### 1.6 前端 CI（typecheck + lint + test + build）

```bash
npm --prefix apps/frontend-nextgen run ci
```

首跑（Task 19 carry 补跑）：lint FAIL——`botAuthorityService.ts` 8 处
`@typescript-eslint/no-use-before-define`（`botAuthorityState` 定义在使用之后）、
`botAuthority.test.ts` 4 处 `jest/no-conditional-expect`。这正是 Task 19 留给
Task 20 的 lint 债。修复（`botAuthorityService.ts`：状态常量上移到 service 对象
之前；`botAuthority.test.ts`：4 处 `if (!res.ok) expect(...)` 改为早抛 narrowing +
无条件 expect）后重跑：

```text
exit=0：tsc --noEmit 通过、max lint 0 error、jest 全绿、build 成功
```

### 1.7 e2e python/bash 工具链

- `python3 -m pytest scripts/test_adapters_endpoint_coverage.py`：**5 passed**
  （2 既有 parse_hits 用例 + 3 新增 team slice 用例：3 个真实 endpoint 的发现、
  PUT/POST/DELETE 三方法计数、未挂前缀模板不得误匹配）。
- `scripts/adapters_endpoint_coverage.py` 新增 `--extra-router PREFIX:FILE`
  （默认分母不变；解析同一 `.route` 扫描 + nest 前缀对齐访问日志 MatchedPath，
  无手工 covered 标注）。默认 `--min 100` 门禁分母保持 bcs-http router.rs，
  未删任何分母。
- `scripts/e2e-test/bot_authority.sh`：5 个新 story 注册进 `e2e.sh`
  （`bot_authority` suite：mine 标签、manager grant/revoke、转交全链、team
  slice（凭证命中或 404/401 skip 如实记账）、maintenance binary 用法探测）。
  `e2e.sh -l` 列表输出己验证；`bash -n` 全部通过。未在本会话启动 singlebox
  栈做活体运行，见 §2 未验证项。
- `stories.sh`：`story_user_prepares_agent_network` 内新增
  `_story_mine_items_carry_access_relation_labels`（/bots/my 每项必须有
  owner/manager 标签——分支合同演进的既有 story 内嵌断言，非新 story）。
- `cli-stories.sh`：新增 `story_cli_operator_no_ownership_maintenance_leaf`——
  `bcs-cli --help` 动态发现 25 个叶子，断言无 ownership/migrate/cutover 叶子
  （runbook“bcs-cli 不新增 leaf”的可执行证明）。真实 runbook 主张的实现差异：
  **bcs-ownership-migrate 是独立 governance binary，不是 bcs-cli leaf**
  （下面 §5）。

### 1.8 bcs-cli leaf 动态发现（无手工 covered）

`bcs-cli --help`（`/shared/cargo/target/debug/bcs-cli`）实际输出 25 个命令：
health, connect, onboard, list, get, discover, update-status, request-group-help,
confirm-group-help, create-group, collaboration, get-group, fuse, list-groups,
add-member, chat, group-status, terminate-group, friend, channel, visibility,
session, service, …help。**无任何 ownership/manager/transfer/team/migrate leaf。**
前端权限操作走 workspace service（不经 CLI）；治理走 BCS 深链（owner 由
API 持有）。

### 1.9 运维维护 binary 用法合同（真实子进程执行）

```text
bcs-ownership-migrate inspect（无 --maintenance）      exit=2（usage 语义，Task 17 修复后）
bcs-ownership-migrate --maintenance --help              exit=0
bcs-ownership-migrate --maintenance inspect（无 config-dir/BCS_CONFIG_DIR）→ exit=101：
  thread 'main' panicked at config.rs:1588 "No config file found. Use -c <config-dir>..."
```

最后一条是**如实记录的实现瑕疵**（前况）：缺 config 走的是 config-loader panic
（101），而非 Task 17 的 usage=2 类。它不产生半迁移状态（启动前失败、零写），
但按 runbook“code 2 覆盖 usage 错误”的字面表还差一小步——记录待裁，不虚报。

### 1.10 `git diff --check`

真实结果：`git diff --check` exit=0，无 whitespace 错误（过程中出现过的
trailing blank line 已修复后复验）。

---

## 2. 未执行 / 未验证项（逐项原因）

1. **live MySQL 全家（6 个 `#[ignore]` 套件）**——环境无 MySQL（`127.0.0.1:3306`
   `connect: 拒绝连接`；环境变量 `BCS_TEST_MYSQL_URL` 未设）。已按要求真实
   尝试 `-- --ignored` 并记录失败形态：

   | 测试 | 所在 | `-- --ignored` 真实输出 |
   | --- | --- | --- |
   | mysql_authority_schema_conformance | bootstrap/bcs/tests/bot_authority_mysql.rs | panic: `BCS_TEST_MYSQL_URL must be set ... NotPresent` |
   | mysql_manager_mutation_concurrent_revocation_lock_order | 同上 | 同上 |
   | mysql_accept_race_across_two_instances | bootstrap/bcs/tests/bot_ownership_transfer_mysql.rs | 同上 |
   | mysql_idempotency_race_across_two_instances | 同上 | 同上 |
   | mysql_pending_slot_race_across_two_instances | 同上 | 同上 |
   | mysql_ownership_migration_conformance | bootstrap/bcs/tests/ownership_migration.rs | 同上 |
   | mysql_two_instances_same_key_single_committed_operation | bcs-edge-permission-store/tests/team_manager_sync.rs | panic: `valid BCS_TEST_MYSQL_URL: Parse(...)`（需要真实 DSN） |
   | mysql_two_instances_concurrent_move_single_committed_operation | 同上 | 同上 |
   | mysql_ownership_lifecycle_conformance | bcs-bot-store/tests/ownership_lifecycle.rs | panic: `BCS_TEST_MYSQL_URL must be set ... NotPresent` |

   即本环境全部记为**未验证：无 MySQL**（不作为通过报告）。CI 有 MySQL service
   才执行；凭据不落日志/文档。
2. **singlebox 覆盖率栈（门禁二件套）**：脚本存在且本次**真实尝试启动**
   （`bash singlebox/ci/singlebox_coverage.sh`，2026-10-09 17:07 本机起跑）：
   正常进入 `--standalone setup all`，`bcsfuse` wheel 成功构建，随后进入
   `uv sync --index-url https://pypi.org/simple` 后端依赖下载阶段；在外部网络
   （pypi.org 直连）受限的环境下下载近乎停滞（faiss-cpu/mysql-connector/
   grpcio 等大包 15+ 分钟未完成），本次尝试中止（23 分钟，进程已清），未进入
   bcs 覆盖率编译/验收段。**结果：本环境未跑完 → 报告与 verifier 产物未生成，
   coverage/endpoint/CLI 覆盖率数字未验证，不伪造**。CI 中该栈有镜像源与 MySQL
   service，按既有门禁执行；本任务未降低任何其阈值（§7.6）。
   （证据：`/tmp/singlebox_cov.log` 首启过程中断前 17.5KB 输出；目录
   `singlebox/.dependencies/coverage/singlebox/{raw,reports,mock-services}` 仅为
   空壳初始化。）
3. **clippy**：未跑。前况即 bcs-domain deny-lint 墙（progress.md Task 6 注记）；
   本任务只改测试/脚本/前端 lint 修复，预期不新增 clippy 面，但如实记“未执行”。
4. **深分页 / 大历史 transfer 列表的 EXPLAIN、连接池等待、MySQL 锁等待实测**：
   现有测试没有任何测量点；无实测数字（不虚构）——见 §7 “未测维度”。
5. **bcs-collaboration-store 2 处失败与 clippy bcs-domain 墙**：分支前况
   （§1.5），非本计划回归，本任务不越权修改。

---

## 3. AC01–AC19 矩阵（实现证据所在）

| 编号 | 场景 | 证据位置（已在 §1.1/§1.2 执行路径） |
| --- | --- | --- |
| AC01 | 授予后 B.mine=manager、A.mine=owner | e2e_bot_authority story 3（双向 grant + 双向 mine 断言）；conformance_bot_authority.rs 全套（§1.2 绿） |
| AC02 | owner/manager 重叠、Human row 不重复且标签稳定 | bcs-app-bot list_mine 分页/去重套件（focused gate 绿）+ e2e mine 全项非空枚举断言 |
| AC03 | 交错/多页/空页先并集后分页 | bcs-app-bot mine 分页用例（顺序/total 契约） |
| AC04 | B 以 X view 读 Group/Session 与 A 同 scope | e2e story 4：B(view X) 列到此私有 group；owner_manager_parity.rs（§1.2） |
| AC05 | 无 view 仍 Human 视角；session-only 不升格 | group/session parity 用例 |
| AC06 | X 不在 Session 时 B 不得越权读消息/文件 | session_file_facade.rs `non_member_cannot_list_session_files` 等 |
| AC07 | worker 时 B 无群管理权；driver/manager 时同 owner | owner_manager_parity.rs |
| AC08 | 全资源 owner/manager 对照 + 业务审计 operator/effective actor | Tasks 9–12 各自 RED/GREEN 套件（focused gate 绿）+ §1.1 session/file 流 |
| AC09 | manager 可增删 manager、自撤 direct/ownership_transfer、team-only DELETE=200/revoked=false、终撤后 403 | e2e_ownership_transfer story 7（revoke 响应 remaining_team_sources=[] + 接续 403）+ manager_mutation 套件 |
| AC10 | 越权/Bot-only/跨 env 拒绝 | mutation/manager 路由 conformance + bcs-api-http manager/ownership route 测试 |
| AC11 | profile/friend/后缀不构成 manager | edge permission conformance（Task 3 suites） |
| AC12 | manager 不进 friend 列表、不进 runtime grant | e2e story 4 friend 快照不变 + admission/runtime isolation suites |
| AC13 | grant/revoke 幂等；再 grant 真恢复 | mutate_manager 合同（repo port doc + manager_mutation 用例） |
| AC14 | 写/审计/提交失败全回滚，读失败 fail-closed | authority_transaction_capability.rs（committed_steps == 3 等真实断言） |
| AC15 | 失权后新/旧 token、WS 入站/出站、replay、fallback、双实例停权 | Task 15/16 suites（bcs-ws 全绿见 §1.2）+ e2e_ownership_transfer WS 撤权断言（dequeued 保护帧停止） |
| AC16 | 独立参与关系保留；FullOnly 只 SkipMessage；真失权才 InvalidateBinding | delivery_authorization 合同测试（Deliver/SkipMessage/InvalidateBinding 三态逐条） |
| AC17 | 混合身份按实时事实验证 | authorization.rs resolve_authorized_principal 分支 + e2e 混合 principal（owner/manager 均 200） |
| AC18 | 删除重建不回收管理权；重启不变 | ownership_lifecycle.rs（memory/sqlite 路径绿；MySQL 案未验证 §2） |
| AC19 | 撤销 manager 后 created_by/is_creator/后缀/重 onboard 不复权 | e2e_ownership_transfer story 7：patch 403 + re-onboard 后 ownership 仍 B + 再 patch 仍 403 |

## 3b. OT01–OT27 矩阵

| 编号 | 证据位置 |
| --- | --- |
| OT01 | e2e_ownership_transfer story 2（发起后 A 仍 owner；B 接受前无群/Session 权限由 parity 套件覆盖） |
| OT02 | accept 后唯一 owner=B、A 保留 ownership_transfer 来源 manager：e2e story 3/7 + store transfer 套件（revoke remaining_team_sources 响应） |
| OT03 | e2e RED 变量 a/b mine 翻转；created_by 不变（e2e 断言 re-onboard 前后 ownership 归属） |
| OT04 | pending 唯一（DB 约束）——sqlite conformance 绿；MySQL 生成列唯一 → CI-pending（§2） |
| OT05 | 同 key 幂等/不同 body 409 | transfer 套件 + 路由 scripted 测试（§1.2） |
| OT06 | 重试不重复转正 | 同上 |
| OT07 | 并发唯一终态 | 同上（sqlite 内存路径）|
| OT08 | 数据库时钟到期 | store 测试（expiry 物化） |
| OT09 | GET/list 只读、expired 投影 | transfer_query 套件 |
| OT10 | 非接收人/manager/Bot-only 不能操作 | e2e 发起人 accept 403 + 路由 403 用例 |
| OT11 | 跨 env/自转 400 | 修正后的 e2e 细节（"owner cannot transfer to themselves" 400 实测） |
| OT12 | owner_changed invalidation 同码 409、终态生效 | Task 14 typed 分支套件（route+application） |
| OT13 | 无部分切换（任一步边写/版本 CAS/回执写失败均回滚） | transfer decide 套件（恒定步预算）|
| OT14 | 前 owner 可继续管理不可再发起；新 owner 可发起 | e2e story 3 + 二次发起 403 断言 |
| OT15 | 撤销后无任何回权路径 | e2e story 7 全链 |
| OT16 | 再 onboard/ensure 不重置 owner | e2e re-onboard 断言 + registration init 合同套件 |
| OT17 | 删除/重建/终端不再执行 | ownership_lifecycle 套件 |
| OT18 | 最小可见字段、不可枚举 | ownership_routes concealment（404 envelope） |
| OT19 | A(manager) 合法 WS 继续；撤销后 dequeue 停止授权 | e2e_ownership_transfer story 6/7 双相 WS 断言 |
| OT20 | owner/manager 不进 friend/runtime grants | e2e friend 快照 + admission isolation |
| OT21 | 重试/丢失响应返回持久回执 | transfer 套件（幂等回执） |
| OT22 | 无 N+1 预算 | §7（1/128/129 计数 + 步预算） |
| OT23 | 迁移原子写、created_by 不改、重跑安全 | ownership_migration / conformance_ownership_migration / apply 合同套件（子进程级二进制演练） |
| OT24 | 无主/多 creator/损坏不自动迁 | 同上（governance reason 固定词表） |
| OT25 | 注册初始化原子（v1/v2/legacy） | registration init 套件（Tasks 5/18） |
| OT26 | 重复注册不覆盖 ownership、锁 owner_scope | 同上 + e2e re-onboard |
| OT27 | cutover 后 created_by 不单独放行 | e2e 全 403 断言 + 生产 cutover 全套管检查（merge probe） |

---

## 4. 迁移候选与冲突处置验证

（证据：`crates/bootstrap/bcs/tests/ownership_migration.rs`（960 行）——真实
`bcs-ownership-migrate` 子进程 + 完整 sqlite 迁移链；`conformance_ownership_migration.rs`；
bot_authority_migration.rs。全部在 §1.2/§1.5 绿。）

- 候选只取未删除 physical bot + 合法 created_by（对照 legacy creator 声明）；
  Human self row/tombstone 排除。
- 机器 reason 固定词表（ready/missing_creator/conflicting_creators/missing_human/
  authority_inconsistent）按报告输出断言。
- 有界批次（>100 拒绝不截断）；同 --batch-id 重放返回原报告；failed 条目 exit=1
  且重跑恢复；不写 created_by；空 confirm-file 走 400/usage 语义（binary 阶段断言 2）。
- `inspect` 是纯 dry-run（前后指纹断言零写）。

## 5. 回滚演练（runbook dry-run 断言）

- runbook `docs/runbooks/bot-authority-cutover.md` 保持 exit-code 表与实现
  一致（code 0/1/2）。本次真实子进程执行见 §1.9：无 `--maintenance` → 2；
  `--maintenance --help` → 0；缺 config 的 panic=101 是需要 runbook 标注的实现
  瑕疵（回滚语义不受影响——启动前失败零写）。
- 反向回滚是产品合同（当前 owner 发起反向转交），不是数据回滚——在合同上由
  transfer 套件固定；本环境对双实例/重启的持久语义由 sqlite/memory 路径绿证实，
  MySQL 双实例未验证（§2）。

## 6. 持续授权成本记录（spec §17.1/§17.2，真实数字）

来源：既有计数器测试（全绿于 §1.2/§1.5）+ 本次新增 e2e 断言；所有数字给出文件与
断言；未测维度如实列于 §6 末尾。Memory lane 按合同以等价 repo 调用计数。

控制面（SQLite/等价 statement 预算，源码即证据）：

- 新建转交：4 读（idempotency/bot 聚合/owner 槽/pending）+ Phase B 4 写
  （锁定/清理/条件 INSERT）+ 1 回执重读 = 9 statement ≤ §17.1 预算 10
  （transfer_create.rs:122/329 两个 vec 逐条可数；Doc 注释“回执重读加一条”）。
- accept/reject/cancel：恒定预算——“最小记录读 + 1 校验读事务 + 每个已提交前缀
  探测一次锁定条件写 + 一次动作事务 ≤7 statement”（transfer_decide.rs:45-56
  文档断言由 Task 8 测试固定），≤ 预算 12。
- manager 撤销事务：`authority_transaction_capability.rs:182/522`
  `assert_eq!(committed_steps.len(), 3)` —— 真实数过。
- team sync：分块 ≤100 成员；审计语句 ≤106 绑参、fresh INSERT ≤ 60×13=780 参数
  （team_sync_sql.rs 文档 + Task 7 套件）；无 N+1。

出站持续授权（Σceil(K/128)+Q_dequeue+Q_redispatch 的实测分解）：

| 维度 | 实测数字 | 出处 |
| --- | --- | --- |
| 一次 ≤128 上下文批次 | **恰好 1 次 `roles_for` 批读**（含 128 上限整批：全网 128 上下文 1 call，且每个 distinct load 1 次） | contract/application/delivery_authorization.rs:396-435 |
| 129 上下文事件 | 服务端拒绝、**零额外读**；adapter 自行 split | 同上 450-471 + protected_delivery.rs:329-411 |
| adapter split 形状 | 1/128/129 三个事件实侧 `batch_sizes == [1],[128],[128,1]` → **ceil(129/128)=2 成立** | bcs-ws/tests/protected_delivery.rs:329-411 |
| 入队后 slow writer | 出队复核按帧执行；`queued_frames_stay_ordered_and_contiguous_under_backlog`、writer 慢不影响他人（paused_authorization 不持注册表锁） | protected_delivery.rs:930-998 |
| 持续授权故障 | 第一个失败事件使两绑定失效；后续 3 次事件**每次恰好 1 次批读**（总计 4 次批读 `[2,2,2,2]`），零帧投递、封闭不重查 | protected_delivery.rs:869-917 |
| 撤权后队列帧 | revoke 在出队复核处真实拦截：e2e story 7（撤销后受保护帧停投，owner 第二连接仍投） | e2e_ownership_transfer.rs |
| 重复上下文 | 同批内同上下文共享证据但逐位判定（1 批读）；不同视角不放行（env 隔离 1 call） | delivery_authorization.rs:473-496/396 |

未测维度（无真实测量点，不编造）：深分页/大收件箱历史的 EXPLAIN 扫描行数；
MySQL 行锁等待时长；共享连接池等待计数；>100 成员的大快照 SQL 压测；E 次/秒
吞吐压测。后两项的合同性预算（§17.2 的 1050/秒示例）在 spec 明确是预算情景，
不是实测承诺。

## 7. 发布核对清单

1. **文件规模**：每文件 ≤1000 行——新增/修改 Rust/TS source：e2e_bot_authority.rs
   924、e2e_ownership_transfer.rs 753、botAuthorityService.ts 527、
   botAuthority.test.ts 358；python/脚本：adapters_endpoint_coverage.py 593、
   test_adapters_endpoint_coverage.py 125、bot_authority.sh 502、e2e.sh 225、
   cli-stories.sh 590。（stories.sh 1804 行为既有大文件，本任务 +43 行；bash
   非生产 Rust source，前况。）
2. **schema/迁移**：001..031 冻结链已有（Task 2/17），无新迁移。
3. **OpenAPI**：两个入口校验 82/26 operations 全过（§1.4）。
4. **conformance**：R25 登记差量由 Task 18 裁决为计划内；arch-check 其余项为
   前况（§1.3）。
5. **endpoint/CLI 覆盖**：team slice 3 方法被发现并可计数（pytest）；默认 100%
   门禁分母未动；bcs-cli 无新 leaf（§1.8）。
6. **/singlebox 基线**：未降低任何既有阈值（本任务零阈值变更；singlebox 未跑
   见 §2）。
7. **不 push、不建 PR、提交无 footer**（按 finish flow）。

## 8. 本任务实际变更文件

```text
A  crates/bootstrap/bcs/tests/e2e_bot_authority.rs       (924)
A  crates/bootstrap/bcs/tests/e2e_ownership_transfer.rs  (753)
A  scripts/e2e-test/bot_authority.sh                     (502)
M  scripts/e2e-test/e2e.sh           （suite 注册：bot_authority 5 story + 列表）
M  scripts/e2e-test/stories.sh       （/bots/my access_relation 标签断言；新 story 注册）
M  scripts/e2e-test/cli-stories.sh  （no-ownership-leaf 可执行断言）
M  scripts/adapters_endpoint_coverage.py   （parse_nested_router + --extra-router）
M  scripts/test_adapters_endpoint_coverage.py（3 个 team slice 发现/计数用例）
M  ../frontend-nextgen/src/services/workspace/botAuthorityService.ts（lint: 状态上移）
M  ../frontend-nextgen/test/botAuthority.test.ts（lint: 4 处 conditional expect）
A  docs/superpowers/plans/2026-10-08-bot-owner-manager-validation.md（本文）
M  docs/runbooks/bot-authority-cutover.md（验收演练记录附录）
```

生产 Rust source 改动：0 行（本任务只加测试、脚本、文档与前端 lint 修复）。