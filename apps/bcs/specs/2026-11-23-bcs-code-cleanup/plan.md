# BCS 重复逻辑清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按[方案 A](spec.md)减少 Group 投影、Event 校验和 CLI 环境解析的维护点，不改变调用者观察到的结果。

**Architecture:** 优先在已有所有者内部去重。数据库查询、错误处理时机和 adapter 的响应构造仍由原入口控制。三个功能簇独立提交和回滚，不为了共用几行代码新增 crate 或让两个 adapter 互相依赖。

**Tech Stack:** Rust / Cargo workspace、`DbPlugin`、Memory / SQLite / MySQL、Serde、Axum、现有 conformance 和 CLI 双 target 测试。

---

**状态：已于 2026-11-23 按本计划执行完毕（任务 0–4、6；任务 5 Manifest 暂缓）。** 源码基线 `bee25769ab`，设计与初始计划提交 `9ce8d15304`；当前修订以本目录文件为准。以下 `crates/` 路径及命令均相对 `apps/bcs/`。只在专用分支提交，不在 `dev` 提交、不推送。不运行全局 `cargo fmt`。实测结果随 PR 报告，不单独提交验证日志；下面的预期不是已取得的结果。

**本批范围：**仅实现 Group、Event、CLI 三个功能簇；Manifest 已暂缓。同 crate 枚举/时间转换、跨 crate 转换、存量 `#[allow(dead_code)]` 扫描和与本次改动无关的死代码均留待后续独立评估；任务 6 只删除本次改动新产生的孤儿 helper/import。

## 任务 0：确定可比较的基线

- [x] 从仓库根目录运行 `cd apps/bcs`；执行 `git status --short --branch`、`git rev-parse HEAD`。确认工作树干净，且当前不是 `dev` 或 detached HEAD。用 `git diff --name-only bee25769ab..HEAD -- Cargo.toml Cargo.lock crates migrations scripts/ci` 确认执行前源码与相关门禁相对基线无漂移；若存在差异，先明确更新基线和文档，不把旧基线输出与新源码混用。在同一工具链/feature 配置下保留基线与最终输出。
- [x] 阅读 [BCS 规则](../../AGENTS.md)、[开发规则](../../../../docs/agents/development.md)、[知识标准](../../../../docs/agents/knowledge-standard.md) 和各功能簇的测试入口。执行 `cargo check --locked --workspace --all-targets` 及下文各簇的定向测试，记录通过、失败、忽略的数量；原有失败先复现，不改 fixture 或 CI 门槛来通过测试。
- [x] 基线运行 `bash scripts/ci/check-deps.sh`、`bash scripts/ci/check-import-rules.sh`，分别记录命中项和退出码。设计阶段已观察到 DEP-1 / DEP-6、LINT-1 失败；不能把整个脚本永久视作豁免。

## 任务 1：Group 查询结果的投影与成员去重

**文件与职责**（前缀 `crates/services/bcs-group-store/`）：

| 文件 | 变更 |
|---|---|
| `src/store_reads.rs` | 单组严格读取，以及 `load_all_groups_from_mysql` 的批量 JOIN 投影；保留原 SQL、读取顺序及错误上下文 |
| `src/store_lists.rs`、`src/store_finds.rs` | 分别将 3 个 JOIN 结果循环的映射交给共享实现；保留每个入口的查询与日志 |
| 新建 `src/store_projection.rs`；`src/lib.rs` | 私有 `mod store_projection;`，唯一 Group 字段构造和批量成员聚合 |
| `src/store_mapping.rs` | 预期不变，直接复用现有 `pub(crate)` 字段解析器；仅在实际可见性编译错误时最小调整，不顺手重构 |
| 新建 `src/store_tests/projection.rs`；`src/store_tests.rs` | 加 `mod projection;`，子模块 `use super::*;` 复用 `RecordingDbPlugin`，不公开测试接口 |
| `tests/conformance_eventful_mutations.rs` | 在现有 `find_by_participant*` SQLite 场景补 Human 持久化 `participant` 后，经分页入口读回 `Full` 的缺列行为断言 |

- [x] **先锁定当前行为。** 在 `projection.rs` 用原入口写 characterization tests；对现有行为应该先绿，不要把重构当成 bug 修复。基础用例可直接使用：

  ```rust
  #[tokio::test]
  async fn joined_rows_keep_first_role_and_encounter_order() {
      let row = |bot: &str, role: &str| DbRow::new(BTreeMap::from([
          ("group_id".into(), Value::from("g")),
          ("driver_bot".into(), Value::from("b")),
          ("opening_message_json".into(), Value::Null),
          ("human_mention_notify_mode".into(), Value::Null),
          ("bot_uuid".into(), Value::from(bot)), ("role".into(), Value::from(role)),
      ]));
      let db = RecordingDbPlugin::with_query_rows(vec![
          row("b", "driver"), row("a", "worker"), row("b", "observer"),
      ]);
      let store = MySqlGroupStore::new(Arc::new(db), "local".into());
      let groups = store.load_all_groups_from_mysql().await;
      assert_eq!(groups.len(), 1);
      let members: Vec<_> = groups[0].participants.iter()
          .map(|p| (p.bot_uuid.as_str(), p.role)).collect();
      assert_eq!(members, [("b", ParticipantRole::Driver), ("a", ParticipantRole::Worker)]);
  }
  ```

- [x] 用 recording harness 补四组输入/断言：(1) 两个组共享一个成员 ID，两个组都保留它；LEFT JOIN 无成员仍返回组；(2) 两个非分页 `find_by_participant*` 的 `g_group_kind/g_dm_pair_key/p_*` 别名解出 DM、角色、tags、view scope，而非静默落回默认值；(3) 同一组第二行的 opening JSON 损坏，**整批为空**；(4) 单组 driver 解码出错或 notify mode 非法返回 `Err`，列表的非法 notify mode 沿用已有回退。再覆盖空结果、相同更新时间按 ID 排序。运行 `cargo test --locked -p bcs-group-store --lib store_tests::projection`；检查匹配数非零。
- [x] 另在 `tests/conformance_eventful_mutations.rs` 用真实 SQLite 固定第五组行为：`find_by_participant_paginated_sql` 的真实 SQL 外层未投影 `message_view_scope`，recording fixture 不得人为补该列；Human 成员即使持久化了 `participant`，也在此入口回退 `Full`。本次不补 SELECT 列、不修该 quirk，并运行 `cargo test --locked -p bcs-group-store --test conformance_eventful_mutations`。
- [x] **提取投影。** 从 `store_reads.rs::load_group_from_mysql` 和 `store_lists.rs::list_paginated_sql` 对照移动 `Group { ... }` 的同一套字段赋值。为 `store_reads.rs` 1 个、`store_lists.rs` 3 个、`store_finds.rs` 3 个共七个 JOIN 的结果定义两组固定列名：普通投影读取 `group_kind/dm_pair_key/bot_uuid/role/actor_kind/mode/tags_json/message_view_scope`；两个非分页 participant find 读取 `g_group_kind/g_dm_pair_key/p_bot_uuid/p_role/p_actor_kind/p_mode/p_tags_json/p_message_view_scope`。由调用方选择，不探测列名，不改 SQL。RecordingDbPlugin 只验证投影器如何读取给定的列；实际 SELECT 的别名、缺列和分页行为由 SQLite 执行原查询验证。严格入口先读 `group_id/driver_bot`，再解 opening message、加载成员、最后严格解 notify；批量入口仍逐行检查 opening message，只有新组才解其余字段。将这些已解析的差异字段传给共同的纯构造函数；不以 `unwrap_or_default()` 替换严格读。
- [x] **替换七个循环。** 用 `HashMap<String, (Group, HashSet<String>)>`，集合只用于判重，不用来生成输出顺序。先成功读取成员 ID 和 role，再判重；首次出现时执行原 `normalize_kind_mode`、tags、`message_view_scope_from_row` 并 push，重复项不覆盖首次角色。聚合结束对组调用 `Group::sort_by_updated_at_desc`。保留坏 ID、坏 opening message 的原分支和日志；`try_find_by_participant_sql` 的严格加载不是第八个 JOIN，不改为容错路径。

- [x] 以一个大组、重复行及同成员跨组的输入再次运行投影测试。对单组 k 个不同成员，预期查重从最多 k(k−1)/2 次向量比较变为 k 次期望常数时间集合插入；额外空间 O(k)，每组独立。此为算法分析，不是线上测速。用 `RecordingDbPlugin::query_sqls` 断言每个 JOIN 入口仍只查询一次；严格读取成功时仍为组与成员各一次，缺组只查一次。严格读取的双查询测试必须用 `query_results` 依次提供组行和成员行，`with_query_rows` 只用于单查询 JOIN 入口，避免两次查询返回同一批错误形状的行。
- [x] 执行 `cargo test --locked -p bcs-group-store --lib`、`cargo test --locked -p bcs-group-store --test conformance_group_repo --test conformance_human_notify_policy --test conformance_eventful_mutations`。通过后仅提交本任务表格所列文件：`refactor(bcs): share group row projection and participant deduplication`。

## 任务 2：复用分页和计数 SQL，不改变选页语义

**文件：** `crates/services/bcs-group-store/src/{store_lists.rs,store_tests.rs,store_tests/projection.rs}`；仅当行数预算需要时新增 `store_tests/recording_db.rs`。依赖任务 1。

- [x] 先在 RecordingDbPlugin 中捕获 `DbStatement` 的 SQL 和参数（保留已有 query 记录）；测试 `count()` 与 `count_by_kind(None)` 均只绑定 env；`list_paginated` 与 `list_paginated_by_kind(None)` 按 `[env, limit, offset, env]`；`Some(Dm)` 按 `[env, "dm", limit, offset, env]`。用 SQLite 混排 normal/DM，至少一个组有两个成员；断言按 kind 过滤后的页大小、成员完整、偏移超过总量返回空。没有内层 ORDER BY 的原查询不承诺额外的页顺序。`store_tests.rs` 基线已为 963 行，新测试体只放入 `projection.rs`；若 harness 扩展后任一变更文件将超过 1000 行，先把 `RecordingDbPlugin` 机械移入 `store_tests/recording_db.rs`，不申请行数豁免。
- [x] 无 kind 的 count 只执行一次 `self.count_sql().await`；有 kind 保持参数化查询与现有零值回退。为两个普通分页入口提取私有 statement builder：只从 `""` 或 `" AND group_kind = ?"` 选择固定片段，插入**内层** `WHERE env = ?` 与 `LIMIT ? OFFSET ?` 之间，外层 `LEFT JOIN … gp.env = ?` 不动。调用者保留各自耗时/错误日志。`list_paginated_filtered_sql` 的内层 `ORDER BY gmt_modified DESC`、participant 分页的 tie-breaker 不得被无排序查询替代。
- [x] 运行 `cargo test --locked -p bcs-group-store`。检查 DB 查询数、SQL 参数序列及排序断言均未回归，再提交 `refactor(bcs): reuse kind-aware group pagination queries`。

## 任务 3：Event Store 共用纯校验，保留后端差异

**文件：** `crates/services/bcs-event-store/src/{lib.rs,memory.rs,db.rs,db/validation.rs}`；新建同 crate 的 `src/validation.rs` 与 `src/memory/{repo,fanout,delivery,retention}.rs`，并同步更新 `crates/services/bcs-event-store/CONTEXT.md`。契约：[Event Store context](../../crates/services/bcs-event-store/CONTEXT.md)。`tests/support/memory_delivery_regressions.rs` 保留既有 Memory 投递回归，由 `--test memory_atomicity` 执行；新增的校验负例集中在 `tests/validation_regressions.rs`，对照 Memory 和真实 SQLite `DbEventStore` 的返回结果，并运行 `tests/conformance_event_store.rs`、`tests/conformance_event_delivery_store.rs`、`tests/conformance_event_store_mysql.rs` 和 `tests/idle_claims.rs`。

- [x] **先解决文件尺寸。** `memory.rs` 基线 2453 行，单删 validators 不足以达到 1000 行。独立机械拆分：保留 `MemoryEventStore`、状态和锁类型在 `memory.rs`；把唯一的 `impl EventRepoPort` 放入 `src/memory/repo.rs`，但将 `claim_deliveries`、`complete_delivery_attempt`、`create_replay_target` 的完整方法体移至 `src/memory/delivery.rs`，并将 `purge_expired` 的完整方法体移至 `src/memory/retention.rs`，四个 trait 方法只做原样委托。跨同级模块调用的自由函数和 inherent helpers 均为 `pub(super)`，不使用同级不可见的模块私有 `fn`。七组共享 validator 在本提交暂留 `memory.rs`，下一提交直接移至 crate 根 `validation.rs`，不做第三次搬家。按下表分配职责与预算（均为目标上限，不是已有实测）：

  | 文件 | 初始函数/责任分配 | 拆分提交目标 `wc -l` |
  |---|---|---:|
  | `memory.rs` | 类型、锁、构造和 commit helpers；`scope_key/event_scope_keys/claim_owner/parse_timestamp_ms`；七组共享 validator 仅在本提交暂留，`validate_replace_subscription/validate_append` 持续保留 | ≤800 |
  | `repo.rs` | 唯一 `EventRepoPort` impl；仅上述四个长方法委托 | ≤850 |
  | `fanout.rs` | `append_event_to_state`、`validate_causation`、`existing_by_producer`、`target_ids_for_event`、`target_id`、`backfill_group_provisioning_targets`、`resolve_causal_target`、`complete_event_fanout_if_settled` | ≤550 |
  | `delivery.rs` | `delivery_is_due/delivery_is_eligible/delivery_dependency_satisfied/delivery_unblocks_lane`、`replay_target/unresolved_replay_target`，以及 `claim_deliveries`、`complete_delivery_attempt`、`create_replay_target` | ≤600 |
  | `retention.rs` | `purge_expired`、`event_can_be_purged` | ≤150 |

  保留所有签名、锁获取顺序、`.await` 和 public re-export，不拆成多个同 trait impl。若实际行数超预算，继续按同一职责提取并更新预算记录，不把无关方法塞进 retention。按知识标准在 CONTEXT **新增** memory 子模块布局说明（现有文档仅描述 `src/db/`），核对实际 `wc -l` 输出。运行 `cargo test --locked -p bcs-event-store` 并以 `refactor(bcs): split memory event repository by responsibility` 独立提交。
- [x] 在新共享模块放边界测试，第一次使用尚未迁入的 `validate_claim` 应失败于缺失符号。原实现迁入后，以下测试应通过。现有共享 conformance 主要覆盖正向状态机，Memory regressions 仅覆盖部分 lease、payload hash 和 limit 负例；不得宣称下列边界已有完整覆盖。需新增共享纯函数边界测试，以及 Memory/DB 各自 store 级首错特征测试：new subscription 的空 id/env 或非法 revision、claim/renewal 的 lease 等于 now、payload hash 错、Retryable + RetryWait 没有未来重试时间、Delivery list limit 0/100/101（含 100 合法）、replay reason 超长、replacement 的 `expected_revision == u64::MAX` / 非 next revision / subscription_id 不匹配，以及 append 的空字段与非法时间戳顺序。DB 侧在 `tests/validation_regressions.rs` 使用真实 SQLite `DbEventStore` 命令对象验证七组函数及 replacement/append 两个共享谓词的返回路径，并核对与 Memory 相同的错误消息；Memory 另用空 id/env + 非 next revision 证明前置首错未变。Subscription list limit 仍是后端各自的内联检查，仅在 store 层做 0/100/101 回归，不纳入共享函数，也不迁移 retention 或 skip/cancel reason 检查。不能只测 SQL 形状、只测纯函数或只测 Memory。

  ```rust
  #[test]
  fn invalid_claim_preserves_first_error() {
      assert!(matches!(validate_claim("", 5, 5, 0, ""),
          Err(EventRepoError::InvalidInput(s)) if s == "claim worker id and env must be non-empty"));
      assert!(validate_claim("worker", 5, 6, 1000, "dev").is_ok());
      assert!(validate_claim("worker", 5, 6, 1001, "dev").is_err());
  }
  ```

- [x] 逐个**移动而非重写** DB 侧七组函数 `validate_claim/validate_lease_renewal/validate_materialization/validate_completion/validate_list_limit/validate_replay/validate_new_subscription`，以及供两个后端部分包装共用的 `validate_replacement/validate_event_command`，到 `src/validation.rs`；改为 `pub(super)`、显式导入 repo DTO/错误、`EventDeliveryStatus`、`sha2::{Digest,Sha256}` 和 `crate::validate_scope`。`lib.rs` 声明 `mod validation;`。DB 和 Memory 按原调用位置导入同一函数；Memory 的 `validate_create_subscription` 对应 `validate_new_subscription`。保留非校验 helper（如 `claim_owner`）的原归属。
- [x] **不可合并的前后置检查：** Memory `validate_replace_subscription` 先拒绝空 subscription id/env，再调用共享 `validate_replacement` 校验下一 revision；SQL 直接调用后者。Memory `validate_append` 先调用共享 `validate_event_command` 完成相同的非空字段循环，再解析两个时间戳；SQL 只调用前者。保持先校验 ID、再校验时间的顺序，不搬运依赖锁、CAS 或读库的检查。用同一条含多种错误的输入锁定各后端既有首错，不把行为差异“修正”为一致。
- [x] 先执行 `cargo test --locked -p bcs-event-store --lib --test memory_atomicity --test validation_regressions`，确保实际编译并执行共享纯函数测试和新增 store 负例，再执行 `cargo test --locked -p bcs-event-store`，单列 `conformance_event_store_mysql` 中实际执行/跳过的测试。若可提供 `BCS_TEST_MYSQL_URL`，另运行 `cargo test --locked -p bcs-event-store --test conformance_event_store_mysql -- --ignored`；否则在 PR 验证说明 明确记录“真实 MySQL 本地未执行，依赖仓库根目录 `.github/workflows/unit-tests.yml` 的 MySQL service”，不得以 SQLite 结果替代该证据。用 `git diff -M --color=always --color-moved=zebra` 配合统一错误消息清单，人工核对错误字符串和边界条件没变；非 TTY 的普通 `git diff --color-moved` 不作为移动检测证据。独立提交 `refactor(bcs): share event repository input validation`。

## 任务 4：CLI 的 lib 与 bin 使用同一份 session/URL 代码

**文件：** `crates/tools/bcs-cli/src/{lib.rs,main.rs,cli/environment.rs,cli/tests.rs,cli/tests/environment.rs}`、`crates/tools/bcs-cli/CONTEXT.md`；新建 `src/cli/environment/shared.rs`。现有 [CLI context](../../crates/tools/bcs-cli/CONTEXT.md)。`main.rs` 与 `lib.rs` 是两个 crate root，`pub(crate)` 不能直接跨 target 调用。

- [x] 先在现有 `--lib` 和 `--bin bcs-cli` 测试分别固定两层不同行为：(a) clap 解析入口中，显式 `--url` 优先；未显式传参时 `MOLTIS_BCS_URL` 被捕获进 `cli.url`，因此先于 `BCS_API_BASE_URL`，两个环境变量同时设置时必须断言 MOLTIS 胜出；(b) 两个 target 的 resolver 包装均手工构造 `url: None`，成对固定 `BCS_API_BASE_URL > MOLTIS_BCS_URL > session.api_base_url > session.bcs_url`，bin 必须显式断言 `BCS_API_BASE_URL` 胜 session，不能只测 lib。lib 再通过注入 pre/prod 默认值固定 `session > 编译分发默认值 > localhost`，bin 另行固定自身 compiled/local 分支。两个包装因 `Cli` / `GlobalArgs` 参数类型和默认值注入不同而保留，不纳入八函数共享范围。不得把 resolver 的局部优先级误写成真实二进制的端到端优先级，也不得为迎合原文修改 clap 属性。

- [x] 在 resolver 层固定：高优先级 URL 非法即返回原错误，不回退；缺文件为 `Ok(None)`，坏 JSON 是解析错误；同一 session 同时含两个 URL 字段时 `api_base_url` 优先。保留 bin `main.rs` 当前对 resolver 结果使用 `unwrap_or_default()` 的行为，不把 resolver 错误返回改写为端到端退出行为。使用现有串行 env fixture 并恢复环境，不能依赖开发机的 session。纯函数断言示例：

  ```rust
  assert_eq!(normalize_bcs_api_url("wss://example.org/base/ws/bot?q=1#f").as_deref(),
             Some("https://example.org/base"));
  assert!(normalize_bcs_api_url("file:///tmp/a").is_none());
  ```

- [x] 在同一 package 内用 `#[path = "cli/environment/shared.rs"] mod shared_env;` 分别从两个 crate root 加载私有纯逻辑。`SessionInfo` **仍由 lib 定义并公开**；两个 root 的 shared 模块均通过 `super::SessionInfo` 取得相同类型（bin root 私下导入 `bcs_cli::SessionInfo`）。移动 `session_file_path/get_optional_session_file_path/load_session_info_from_path/load_optional_session_info/normalize_bcs_api_url/normalize_url_from_source/resolve_env_bcs_url/resolve_session_bcs_url` 的等价函数体；按调用点使用 crate 内可见性。bin `cli/environment.rs` 仍是命令的入口，重新导出 bin 所需的私有名字，并直接复用 `bcs_cli::get_current_env`。lib 保留原公开路径。不要引入新的公共函数或把 `Cli`、日志 target、网络探测带进共享模块。

- [x] 保持 binary 独有的 `normalize_bcs_ws_url`、`NetworkEnv`、鉴权流程与日志；lib 的 `resolve_bcs_url_with_distribution_defaults` 仍可注入测试默认值。为保留 `main.rs:190-204` 声明的内部 `#[path]` overlay 复用方式，bin `cli/environment.rs` 对共享模块的引用必须是 `super::shared_env`，禁止新增 `crate::shared_env` 等只在普通 bin root 成立的路径；overlay 仓库不可用，最终在 PR 验证说明 单列为未验证项，不宣称已编译验证。`write_session_file` 只是两个测试 target 的 fixture，本批保留各自实现，不新增 `session_fixture.rs` 或测试专用跨 target 路径模块。

- [x] 同步修正 `main.rs` 配置注释和 CLI `CONTEXT.md` 的 URL 优先级，区分 clap 捕获与 resolver 两层；修正 `lib.rs` 顶部仍声称与 `bcs-admin` 共享的陈旧描述。运行 `cargo test --locked -p bcs-cli --lib`、`cargo test --locked -p bcs-cli --bin bcs-cli`、`cargo test --locked -p bcs-cli --test cli_decomposition_contract`，并用 `cargo check --locked -p bcs -p bcs-http --tests` 检查实际使用 `bcs_cli` 的公开库消费者；`bcs-admin` 不直接依赖 `bcs-cli`，不再把检查该包当成本任务的行为证据。确认公开库调用路径、离线 preview 和错误输出不变；提交 `refactor(bcs): share CLI session and URL resolution across targets`。

## 任务 5：Manifest 已暂缓（非实施任务）

决策见 [spec §5.4](spec.md#54-manifest-工具暂缓)。本批保留两个 HTTP adapter 的私有实现，不新增 `bcs-config-api` 公开方法，不调整版本或 API-1 脚本。无需等待批准即可继续任务 6。后续若恢复，须独立确认版本策略和 consumer 影响。

## 任务 6：有证据的删除、最终验证与回滚

- [x] 对因上述改动失去调用者的每个私有 helper/import，先运行 `git grep -n -w -e parse_actor_kind -- .`（仓库根目录示例；逐项更换检索名），核对 feature、测试、宏和 re-export 后再删除。比如 `MySqlGroupStore::parse_actor_kind` 在 `store_tests.rs` 中被调用，不是本次改动造成的死代码，应保留。不做同 crate 或跨 crate 时钟、枚举转换、授权错误的批量迁移，不扫描与本次改动无关的存量 `#[allow(dead_code)]`，不碰迁移、fixture 和 CI baseline。没有符合条件的删除则不创建空提交。
- [x] 从 `apps/bcs` 运行以下收口检查；按项记录退出码，区分已知失败、新失败、环境不可用及跳过。失败的测试先定位再决定回滚哪一簇，不把 `cargo check` 当作测试通过。

  ```bash
  cargo check --locked --workspace --all-targets
  cargo test --locked --workspace
  bash scripts/ci/check-store-boundaries.sh
  bash scripts/ci/check-port-purity.sh
  bash scripts/ci/check-forbidden-symbols.sh
  bash scripts/ci/check-deps.sh
  bash scripts/ci/check-import-rules.sh
  BCS_BASE_REF=bee25769ab bash scripts/ci/check-public-api.sh
  ```

- [x] 经用户确认可安装组件时运行 `cargo clippy --locked --workspace --all-targets`；无法安装或执行时在 PR 验证说明 标记 Clippy 未验证及原因，不省略该项。`check-public-api.sh` 若因工具缺失退出 77，只记为环境跳过。本批不预期新增任何公开方法；用源码 diff 确认 `bcs-config-api` 和两个 Manifest handler 未改动，不把脚本空输出单独当作 API 验证通过。
- [x] 从 `apps/bcs` 运行 `git diff --check bee25769ab -- .` 和 `git diff --name-only bee25769ab -- .`，逐文件核对变更均属于三个功能簇及其验证、本 spec/plan，不能以 `diff --check` 代替无关文件审查，也不把其它 app 的本地改动纳入本计划。对变更新增/修改的 Rust 源文件逐一 `wc -l`（均 ≤1000），核对公开符号与 Cargo manifests diff、无额外依赖、未改 schema 和协议。把每簇的实际 SHA、测试命令/匹配数、失败或跳过原因写进 PR 验证说明，单列真实 MySQL、内部 overlay、Clippy 和 API-1 的可判定证据或缺口；记录 Manifest 已暂缓，不把它列为交付内容。
- [x] 回滚单位为各簇独立提交；Group 分页依赖投影，先回滚分页；Event 校验依赖内存模块拆分，先回滚校验。无需数据库回滚。提交仅限专用分支；推送、PR 和部署不在本计划内。
