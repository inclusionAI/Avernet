# BCS Bot 头像功能验证与交付记录（Task 8）

- 日期：2026-11-03。
- 分支：`bot-avatar-impl-bcs`。链上提交：T1 `4fb7a510cf`、T2 `d193dffd30`、T3 `b5d99b3109`、T4 `f4e13e03e2`、T5 `3e89de01e0`、T6 `ed397ad10e`、T7 `ef9510d6f2`、fixture 修复 `35fe9020af`、T7 复审修复 `2a37635cfa`。
- 代码基线：`a9b62a1f2`（merge-base 与 `origin/dev` 一致）；设计文档基线 `abac1e6d35`。
- 本 Task 8 提交：见本文件提交（`test(bcs): verify avatar contracts and migration compatibility`）。
- 交付形态：上述 10 个提交的系列已压制为本文件所在 PR 分支上的单一提交交付；本行及之后的验证记录均基于压制前提交链（最终 `a617b8ab51`）实测产生，压制后树内容除本行外与验证时一致。
- 状态：BCS 侧输入与输出完整实现并由下述命令实测验证；可由模拟 Backend 请求独立验证，不依赖 Backend repair 路由上线。部署顺序必须先 BCS 后 Backend。

## 1. 实测结果（命令、退出码与关键数字）

| 命令 | 退出码 | 关键数字 |
| --- | --- | --- |
| `cargo check --manifest-path apps/bcs/Cargo.toml --workspace --all-targets` | 0 | 全 workspace（含全部测试目标）编译通过；无本任务新增 warning |
| `ct -p bcs-protocol -p bcs-bot-store -p bcs-bot -p bcs-app-group -p bcs-app-session -p bcs-http -p bcs-api-http` | 非 0 | 89 个测试二进制 ok；仅 bcs-bot-store `conformance_bot_control_plane_repo` cases_2/cases_3 失败（§6.7 基线复核为既有失败） |
| `ct -p bcs-ws -p bcs-service-api -p bcs`（bootstrap） | 0 | 119 个测试二进制全部 ok |
| RED→GREEN：`ct -p bcs-api-http --test avatar_response_contract` 与 `ct -p bcs-http --test avatar_response_contract` | 0（RED 时非 0） | RED 阶段唯一失败项为 `legacy_patch_group_keeps_participants_unchanged`（`participant.get("avatar_url").is_none()` 失败，即复审 Item A 的 legacy PATCH 泄漏）。将 `handlers.rs`/`projections.rs` 临时还原后该用例再次精确失败于同一断言，恢复修复后 bcs-api-http 6 用例 + bcs-http 3 用例全绿（两半文件由 manifest 边界契约分开，见 §8）。 |
| `bash apps/bcs/scripts/ci_test.sh --fast-fail --base abac1e6d353fa0509b0c658a68d9a41e79ac85bc --head HEAD` | 100 | `cargo nextest` 全 workspace：**8676 运行、8673 通过、3 失败、63 skipped**；3 个失败 = conformance cases_2/cases_3（基线既有）+ `bcs-http admin_invocation_terminal::tests::matching_bot_terminal_dispatches_callback_once`（全量并发负载下 15s 无回调连接；隔离运行 6/6 通过，详见 §6.8）。两次完整运行结果完全一致 |
| `ct -p bcs-admin --bin bcs-admin migrate::mysql_chain_tests::full_mysql_migration_chain_applies_and_preserves_history -- --ignored`（`BCS_TEST_MYSQL_URL` 指向一次性 MySQL 9.7 库） | 0 | **1 passed / 0 failed / 0 ignored**：全链（001–040）真实 MySQL 应用 + 历史校验 + EXPLAIN 断言 + Group/A2A 合同，且 `Bot profile: existing-only avatar/profile MySQL contract passed` |
| Python OpenAPI 契约：`uv run --with pytest --with pyyaml --with jsonschema --with requests --python 3.12 python -m pytest tests/openapi -q`（apps/bcs 下） | 1 | **171 passed、3 failed**；3 项与任务简报记录一致（`test_bot_self_is_internal_and_authenticates_bearer_in_bcs`、`test_group_detail_uses_human_or_owned_bot_resource_access`、`test_update_session_participant_accepts_bot_and_human_modes`，均为 403 描述/bearer 措辞），无新增失败 |
| `bash singlebox/ci/singlebox_coverage.sh`（`BCS_E2E_CHANGED_BASE_REF=a9b62a1f2`，即 merge-base） | 0 | **gate passed**：runner 输出的 `coverage_modules` 全部 13 个条目（bot_dormant、tc_file_upload_integrations、auth、devices、access、bot_chat、bot_collaborator、manager_sync、ownership_sync、cron、expert_chat、files、harness）非回归通过，Backend acceptance 故事全绿；E2E line 53.13%（≥41）、method 47.11%（≥36）、**changed-line 69.91%（1055/1509，>60%）**、endpoint 173/173=100%、bcs-cli leaf 66/66=100%；测量历程与本地一次性 flake 见 §7 |
| `python singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/singlebox/reports`（与上一次 runner 生成的同一 reports 目录） | 0 | `singlebox coverage artifacts verified`；backend 49.37%（≥38）、BaaS 45.94%（≥45）；BCS runtime line/method/router/cli = 53.13%/47.11%/100%/100%；backend router 命中 1046/plugin 证据 9；BaaS router 命中 296 |
| WS 帧回归：`ct -p bcs-ws --test group_session_ws` | 0 | 4 用例全过；会话绑定 connect 帧在携带 `avatar_url` 的 `SessionParticipant` 输入下输出 `WorkbenchParticipantView` 行且无任何 avatar 字段 |

注：`ct` 即 `cargo test --manifest-path apps/bcs/Cargo.toml`。

### RED→GREEN 证据（avatar_response_contract）

1. RED：新增回归（旧/新路由 fixture 各自驱动真实 router）后，首次运行 `8 passed / 1 failed`，唯一失败：

   ```text
   test legacy_patch_group_keeps_participants_unchanged ... FAILED
   panicked at avatar_response_contract.rs:… assertion failed: participant.get("avatar_url").is_none()
   ```

   该失败精确暴露复审 Item A：legacy `PATCH /groups/{id}` 直接序列化共享 V1 `GroupDetail`，导致旧面响应参与者行新增 `avatar_url`。
2. GREEN：`routes/groups/projections.rs` 引入 `LegacyGroupParticipant`（旧字段集 DTO）与 `v1_group_detail_to_legacy_update_json`，`handlers.rs` PATCH 出口改为旧版投影后，该用例转绿；其余全部面（V1 list/public/get/create/update/成员操作、session list/get/create/update/成员操作、WS 帧、RegisteredBot 记录）保持通过。
3. 回归有效性复核：将两处生产文件临时 `git stash` 还原后，`ct -p bcs-http --test avatar_response_contract` 复现同一断言失败（1 failed = `legacy_patch_group_keeps_participants_unchanged`；还原前后其余 2 用例持续通过），恢复修复后全部通过。该回归最初按计划落在 bcs-api-http 测试内；因 bcs-api-http 的 manifest 边界契约禁止依赖 legacy 适配器（`boundary_contract.rs::manifest_does_not_depend_on_legacy_or_concrete_bcs_crates` 拒绝 `bcs-http` 依赖项，且不得弱化该 gate），把旧面 fixture 半边移入 `bcs-http/tests/avatar_response_contract.rs`，V1/WS DTO/存储记录半边保留在 `bcs-api-http/tests/avatar_response_contract.rs`，两文件互为同主题的“新旧 fixture”对照。

## 2. 真实栈实物验收（步骤 4）

环境：`target/debug/bcs -c <临时配置>`（`bind=127.0.0.1, port=0`，`BCS_AUTH_MOCK=1`）+ 一次性的真实 MySQL 9.7 实例（127.0.0.1:3399，新库 `bcs_avatar_t8_live`），通过 `bcs-admin db migrate --dialect mysql --apply --yes` 预先应用全链后启动。人工身份头 `X-Mock-User-Id: 100000`；V1 面带 `X-Avernet-Principal`（HS256 principal）。以下为 curl 级请求与响应摘录（重复执行 3 次结果一致；完整记录见提交说明中引用的验证脚本输出）：

1. 迁移链（真实 MySQL，bcs-admin）：

   ```text
   - 040 bot_avatar checksum=d4fcf858d29841ee23d4bae381592c95d67e64cadb1c678ff4dfb2ba27c715e5
   SHOW COLUMNS FROM bcs_bots LIKE 'avatar_url';
   -> avatar_url  text  YES  NULL
   ```

2. 正常注册与 onboard：

   ```http
   GET  /register/token                      -> token
   POST /register?token=…&bot-name=t8-avatar-…
        -> {"bot_token":"1e53…","bot_uuid":"bot_aae99627"}
   POST /bots/onboard  (X-BCS-Bot-Token)
        -> {"onboarded":true,"bot_uuid":"bot_aae99627"}
   ```

3. admin 更新头像（`avatar_url` 字符串）：

   ```http
   POST /admin/bots/onboard {"bot_id":"bot_aae99627","name":"t8-avatar-…","avatar_url":"https://cdn.example.com/t8/avatar.png"}
        -> {"onboarded":true,…}
   SELECT avatar_url FROM bcs_bots WHERE bot_uuid='bot_aae99627';
        -> https://cdn.example.com/t8/avatar.png
   ```

4. V1 Group 读回（201 Created 与 GET 详情、参与者数组带头像）：

   ```http
   POST /openapi/v1/collaboration/groups {"group_kind":"normal","name":"T8 Acceptance","driver_bot_uuid":"bot_aae99627","participants":[],"collaboration":{"strategy":"chat"}}
        -> 201 {"data":{"participants":[{"actor_id":"bot_aae99627","actor_kind":"bot","name":"t8-avatar-…","avatar_url":"https://cdn.example.com/t8/avatar.png","role":"driver",…}],…}}
   GET  /openapi/v1/collaboration/groups/{group_id}
        -> 200 {"…"avatar_url":"https://cdn.example.com/t8/avatar.png"…}
   ```

5. V1 Session 读回（创建 + 会话列表 + 详情）：

   ```http
   POST /openapi/v1/collaboration/groups/{group_id}/sessions {"kind":"chat"}
        -> 201 {"data":{"participants":[{"…","avatar_url":"https://cdn.example.com/t8/avatar.png"}],…}}
   GET  /openapi/v1/collaboration/groups/{group_id}/sessions?limit=20&view_bot_id=bot_aae99627
        -> 200 两个会话，items[].participants[].avatar_url 均为 "https://cdn.example.com/t8/avatar.png"（列表 name 为会话内成员快照 "t8-avatar-…"）
   ```

6. 清空（`avatar_url: ""`）→ 再次读回（字段保留、值为 null）：

   ```http
   POST /admin/bots/onboard {"bot_id":"bot_aae99627","name":"t8-avatar-…","avatar_url":""}
        -> {"onboarded":true,…}
   SELECT avatar_url IS NULL FROM bcs_bots WHERE bot_uuid='bot_aae99627';  -> 1
   GET  /openapi/v1/collaboration/groups/{group_id}                       -> "avatar_url":null
   GET  /openapi/v1/collaboration/groups/{group_id}/sessions?…&view_bot_id=… -> "avatar_url":null
   GET  /openapi/v1/collaboration/sessions/{session_id}                   -> "avatar_url":null
   ```

7. 不存在 Bot 的 admin 更新不得新增任何行数：

   ```http
   POST /admin/bots/onboard {"bot_id":"t8-missing-ghost","name":"Ghost","avatar_url":"https://cdn.example.com/t8/ghost.png"}
        -> {"onboarded":false,"message":"Bot 未在协作网络注册，请尝试重启"}
   SELECT COUNT(*) FROM bcs_bots;                    -> 2（更新前）→ 2（更新后）
   SELECT COUNT(*) FROM bcs_bots WHERE bot_uuid='t8-missing-ghost'; -> 0
   ```

真实 MySQL 链路（步骤 1 中 bcs-admin ignored 全链用例 + 本节实物链路）均通过；未验证项：无。

## 3. 查询成本（对 Backend 与运维的共用数字）

- 列表/详情每次结果页仅一次 core 批量调用：`BotRegistryCoreService::avatar_urls`（`tests/v1_group_service/avatar_budget.rs::group_list_avatar_query_budget…`、`public_page_avatar_query_budget…` 可执行断言）。
- core 按 256 上限分块：≤`ceil(U/256)` 次存储批量读。300 个不同 ID 的页实测恰为 2 批、300 行（`bcs-bot/tests/bot_profile.rs::avatar_urls_a_300_id_page_is_exactly_two_256_batches`）；257 个 ID + 1 重复亦为 2 批（既有用例）。
- 新增成员名称零查询：列表轻量行 `name` 取成员快照原样，不查 registry；公开页含 Human 成员且 Human ID 从不进入批量提交（`public_page…human_rows_are_never_submitted`）。
- 头像批量读覆盖 NULL：每页重新读取，不使用"缺失才查"分支；读失败映射服务错误，不产生 null 页。
- 既有 driver/peer 名称补齐与 permission/计数查询不在本次预算内，行为未变。

## 4. Schema 版本

- SQLite 与 MySQL 各自 `040_bot_avatar.sql`，仅 `ALTER TABLE bcs_bots ADD COLUMN avatar_url TEXT NULL;`；无索引、无默认值、无回填。
- 历史 migration 未改动：`git diff a9b62a1f2..HEAD -- apps/bcs/migrations` 只包含 README 增补与两个 040 文件。
- bcs-admin 真实 MySQL 全链 gate（§1）与 `bot_avatar_migration.rs` 合同用例均通过；`040` 校验和 `d4fcf858d29841ee23d4bae381592c95d67e64cadb1c678ff4dfb2ba27c715e5`。

## 5. Backend 交付

### 5.1 字段规则（写路径）

| `avatar_url` 输入 | 结果 |
| --- | --- |
| 省略 | 保持已存头像（Keep） |
| `""` | 清空（Clear → NULL） |
| 普通字符串 | 设置（Set） |
| 显式 `null` 或非字符串 | 在 wire/DTO 反序列化处直接拒绝 |
| 目标 Bot 不存在（未注册/软删除） | `ProfileWrite::Missing`：不新增行、不复活，HTTP 表现为 `onboarded:false` |
| 再次注册/更新带显式头像 | 复用 existing-only 写入，不重发凭据、不重新建行 |

### 5.2 existing-only missing 行为

- admin 更新不存在的 Bot：`onboarded:false`，`bcs_bots` 行数不增加（§2.7 的 before=2/after=2、ghost_rows=0 实测）。
- 预检可见但写入瞬间消失（并发软删）同样收敛为 `Missing`，持久化零写入（`admin_onboard_maps_a_missing_profile_write_to_onboarded_false_without_new_rows`）。

### 5.3 已部署版本

- 本 Task 8 提交 + schema 040。**BCS 必须先于 Backend 部署**；迁移 040 需在二进制 rollout 前应用（SQLite 自启动链自动应用）。

### 5.4 可复放的测试请求

Backend 可按 §2 的请求依次验证（使用真实签名 principal 或内部测试头）：

1. `POST /register?token=…&bot-name=avatar-acc` → 取 `bot_uuid`、`bot_token`；
2. `POST /bots/onboard`（`X-BCS-Bot-Token`）→ `onboarded:true`；
3. `POST /admin/bots/onboard` 带 `avatar_url` → V1 面 `GET /openapi/v1/collaboration/groups/{id}`、`GET /openapi/v1/collaboration/groups/{gid}/sessions`、`GET /openapi/v1/collaboration/sessions/{sid}` 读回头像；
4. `POST /admin/bots/onboard` 带 `"avatar_url":""` → 再次读回 `avatar_url:null`（键仍在）；
5. missing 用例：对不存在的 bot_id 发 admin 更新 → `onboarded:false` 且 `bcs_bots` 行数不变。

## 6. 已知限制

1. A2A card 来源注册丢弃 `avatar_url` 的问题已在 PR 3488 复审修复：URL/inline 两个入口均应用显式头像，并覆盖 Keep/Clear/Set、写入失败和 missing 场景（见 §9）。
2. provider 注册已提交后头像读取失败的补救路径按构造 fail-closed，但未以确定性测试钉住（77 个方法 double 成本）。
3. 变更响应可能因头像读取失败在提交后失败（§5.3 预告语义）：实体已持久，重读即可成功；Task 7 复审修复已让 participant 变更响应透传该失败。
4. 内存 store 的 capabilities 保存对损坏/异身份 `bot.json` 记录现在 fail-closed（Task 3 收紧），表现为保存报错而非覆盖。
5. **到期 waiver**：`BCS-AUTHORITY-SIZE-2026-10-10`（注册文件 `docs/arch/waivers/2026-10-10-bcs-authority-file-size.md`）在 **2026-11-10** 到期；范围内 `crates/services/bcs/bot/tests/authority_provider.rs` 当前 1,010 行，超常规 1,000 上限、依赖该 waiver。合并前必须完成拆分或显式续期（不在本任务范围，未由本任务修改）。
6. 本地一次性校验库为 MySQL 9.7：legacy EXPLAIN 链断言需 GLOBAL `explain_format=TRADITIONAL`（该实例已设置）；CI 使用 mysql:8.4，无此问题。
7. bcs-bot-store `conformance_bot_control_plane_repo` cases_2/cases_3 失败为既有失败：已在基线 `abac1e6d35` 独立 worktree 复现（同两用例失败，其余 16 通过），与头像功能无关，未修改。
8. `apps/bcs/tests/openapi` 三个 403 措辞/bearer 描述失败为既有失败（同上一致列表），无新增；`ci_test.sh` 全量失败中的 `admin_invocation_terminal::matching_bot_terminal_dispatches_callback_once` 项在两次全量并发运行中均于 15s 超时，属该用例自述的全量并发负载敏感（TLS 初始化注释），隔离运行 6/6 通过；其模块未被本分支触及，未作为本任务修复。
9. `bcs-ws/tests/web_frame_compat.rs` 为既有的超常规行数测试文件（当前 2,840 行；本系列仅 +1 行），与 §6.5 的 authority waiver 同类登记，供完整性记录，不构成本任务待办。

## 7. 单机环境说明与 E2E changed-line 测量记录

- 首次 `singlebox_coverage.sh` 尝试失败于栈启动：BAAS 设备端口池 20010–20099 被其他会话遗留的 88 个孤儿进程（PPID=1，10 月 29 日启动的 engine-adapter listener）占满（`No free port available in range 20010-20099`）。单机无隔离配置可改（`_process_manager.py` 常量区间）；清理孤儿进程（仅 PPID=1 的遗留 listener，用户进程如 NeteaseMusic 未触碰）后重跑，后续多次运行同样发现该区间被反复遗留，并连同 18800–18899 的 155 个 openclaw 孤儿一并清理。
- 第二跑（栈成功、验收/故事全过、coverage 全收集）失败于 changed-line 门槛：`E2E changed-line coverage 51.45% (2799/5440)`。两个根因均与功能无关：
  1. 默认基线 `origin/dev` 的本地 tip（`2adc74324c`）已包含 #3280 对 `bcs-cli/src/main.rs` 的 3,079 行重写，属 target-only 差异被直接树 diff 误计入本分支（仓库 pre-push 规范明确要求 merge-base 语义）。按规范改用 `BCS_E2E_CHANGED_BASE_REF=a9b62a1f2`（merge-base）后，真实分母为 2,369 行；
  2. T1 拆分产生的 `bcs-bot-store/src/memory_identity_ops.rs`、`memory_runtime_ops.rs` 与 T3 的 `memory_profile.rs` 在 SQLite E2E 栈中结构性不可达（e2e 选中 persistent repo），合计 852 可测行、0 E2E hits。其拆分前母文件 `memory_bot_repo_port.rs` 本就在 gate 的 `e2e_unreachable_memory_repos` 清单内；按该清单对 collaboration-store 拆分文件（2026-11 先例注释）的同一维护方式补入三行并注明测量数据。
- 修正后第三跑（同一次运行的真实栈 + 实测 cobertura）：`E2E changed-line coverage 69.38% (1047/1509) > 60%`、line 53.17%、method 47.11% 全部达标；但 `story_user_operates_group_workspace` 在 persistent group message 步骤偶发 `queue_draining: existing work in the target lane must settle`（400）早退，导致 endpoint 覆盖 171/173（`/groups/{id}/chat`、`/groups/{id}/callback` 两点未触达）而失败——该消息投递时间性 flake 与头像 diff 无关（相同二进制的第二跑该故事全绿且 endpoint 100%）。
- 第四、五跑 endpoint 同样 171/173；第四跑另有 Backend acceptance 失败（BAAS device 端口池 18800–18899 被 155 个历史遗留 openclaw 孤儿进程占满，`No free port available in range 18800-18899`）。清理该区间孤儿进程后 acceptance 恢复。
- 连续三跑的同一步骤失败最终定位为**本地 runtime 状态累积**：重复运行共享的 git-ignored `singlebox/.dependencies`（BCS sqlite 持久队列、BAAS/backend DB）在多次运行后残留非终态队列行干扰同 lane 消息准入；CI 每次均为全新 checkout/全新 `.dependencies`。按此清理 `singlebox/.dependencies`（一次性运行时状态，`.gitignore` 明确排除）后第六跑**全绿**（`singlebox coverage gate passed`，endpoint 173/173，见 §1 数字）。
- 最终采用第六跑结果（§1 表）作为交付数字；第六跑之后立刻由 verifier 对同一 reports 目录校验通过。
- 端口区间为 `apps/baas/.../_process_manager.py` 常量，无隔离配置可改；上述清理只针对已死会话（PPID=1）的端口 listener，实施前确认 21000/8889/8890 等栈端口无存活服务。

## 8. Pre-merge 跟踪

1. **service-api 版本已修复为 2.0.0**：新 trait 方法虽然带默认实现，但 `AdminBotOnboardCommand`、`RegisterProviderBotCommand`、Actor/Participant 和 Group/Session summary 新增公开字段会破坏旧 Rust struct literal；serde 默认值只提供反序列化兼容，不能证明 Rust 源码兼容。因此更正此前“纯新增可升 1.1.0”的建议，按 major 版本升级，同时更新 Cargo.lock。所有 workspace consumer 使用 path 依赖；迁移构造点见 `bcs-service-api/CONTEXT.md`。HTTP v1 字段增量语义与部署顺序保持不变。`bcs-protocol` 随 workspace 版本管理，本轮不扩大为整个 workspace 发布。现有 PR 描述已含传播分析；外部 consumer 需按新字段重编译。
   - 本机未安装 `cargo-public-api`，未声称执行完整 API diff。当前 `architecture-checks.yml` 仅监听 main 的 PR/push 与手动触发；本 PR 目标为 dev，不能把此项描述成已发生的 PR CI 阻断。
2. **waiver 到期处置**：见 §6.5（2026-11-10 前完成拆分或续期）。
3. 既有失败治理均不属于本任务：conformance cases_2/cases_3、OpenAPI 三项措辞、admin_invocation_terminal 负载敏感超时——留待对应模块负责人；若对合并形成阻塞，按仓库既有流程另行记录。

## 9. PR 3488 复审修复（2026-11-03）

实施范围见 [review-fixes.md](review-fixes.md)。三个修复均先以真实服务/文件/SQLite 回归测试复现，再修改实现：

- existing-only profile Core 恢复 Provider 父身份保护；AgentPass 与 AgentPassSubbots 的头像更新保留父身份，换绑拒绝且头像不变。身份校验期间删除的目标经严格读确认后仍返回 `Missing`，读取失败不吞掉。
- memory/file 的完整记录改写共享原 control-plane 写锁，覆盖资料、头像、token、名称/内部属性、creator、visibility、创建和删除。临时文件准备与同步在 blocking worker 中完成，调用方持锁原子替换后发布内存；取消准备不会在锁释放后延迟覆盖正式文件。
- A2A URL/inline Card 注册按原 ownership/Card 原子边界提交，再应用显式 Set/Clear，Keep 不新增写入。失败返回错误但保留已提交注册，不新增回滚或重试。Bootstrap 显式注入现有 registry core。

新增 15 个测试函数（身份 3、A2A 5、文件并发/失败 6、取消 1）。身份测试覆盖两种 AgentPass 模式，A2A 测试覆盖两种 Card 来源。没有 schema、migration、wire 字段或 Service API trait 改动。

本轮已执行的检查（命令在 `apps/bcs` 下运行）：

| 检查 | 结果 |
| --- | --- |
| `cargo check --workspace --all-targets` | exit 0；保留既有 warning |
| `cargo nextest run -p bcs-bot -p bcs-app-group -p bcs-app-session -p bcs-app-a2a-bot --no-fail-fast --retries 0 --test-threads 8` | 最终代码 725/725 passed，0 skipped、无重试 |
| `cargo test -p bcs-bot --test provider_registration` | 32 passed，含已删除 Provider 目标的 `Missing` 回归 |
| `cargo test -p bcs-bot --test bot_profile` | 54 passed |
| `cargo test -p bcs --lib` | 365 passed、1 ignored |
| `cargo test -p bcs-app-a2a-bot` | 27 passed |
| `cargo test -p bcs-bot-store --test memory_profile_concurrency` | 6 passed |
| `cargo test -p bcs-bot-store --lib cancelled_preparation_never_publishes_a_late_record` | 1 passed |
| `cargo test -p bcs-bot-store --no-fail-fast` | 438 passed、2 failed、6 ignored；失败为 §6.7 已登记的同两例 |
| `bash scripts/ci/check-store-boundaries.sh` | exit 0 |
| `bash scripts/ci/check-forbidden-symbols.sh` | exit 0 |
| `bash scripts/ci/check-import-rules.sh` | exit 1；156 处匹配均可在修复前 HEAD 找到，没有新增匹配代码 |
| `bash scripts/ci/check-deps.sh` | exit 1；既有 `bcs-protocol → bcs-domain` 和 `bcs-service-api → bcs-config-api/bcs-storage-api` 与脚本允许表不符；本轮没有改 manifest，`cargo-machete` 未安装 |

本轮所有 16 个修改/新增 Rust 源文件均检查行数，最大为 1,000 行；`git diff --check` 通过。
独立复审已检查身份保护、A2A 编排、文件写锁顺序以及失败/取消处理，没有发现新的逻辑问题。

存储全量失败用例为
`cases_2::persistent_capability_save_preserves_patched_internal_attributes` 与
`cases_3::memory_control_plane_list_by_task_modes_filters_visibility_status_user_visibility`，
断言分别仍为 `Lifecycle Attributes`/`Lifecycle Attributes Saved` 与缺少 `pub-online row`。
本轮没有为这些既有失败修改实现、测试或门禁。真实 MySQL、完整 Singlebox/E2E 未重跑；
前文的对应结果属于原功能验证，不能视为本轮修复后的新结果。

## 10. 桌面评审意见核实与处理（2026-11-03）

依据 `pr-3488-review.md` 对提交 `29352dcd17` 的复审，逐项核实后处理：

| 意见 | 核实与处理 |
| --- | --- |
| service-api 未升级 | 属实。升到 **2.0.0** 并同步唯一 Cargo.lock；八个既有公开结构体新增字段，旧 literal 构造会报 E0063，不能按纯 additions 升 minor。workspace consumer 均为 path 依赖，迁移说明写入 `bcs-service-api/CONTEXT.md`。 |
| 架构 CI 会阻断本 dev PR | 表述不准确。现有 `architecture-checks.yml` 自动触发仅针对 main；版本维护仍按破坏性 Rust 源码变更处理，本轮不修改 CI 触发范围。 |
| 第二笔提交后 Singlebox 未验证 | 已查询 GitHub：**29352dcd17** 的 `Singlebox coverage`、`BCS unit tests` 均 success；该结果只证明此已推送提交，不代替当前未提交修改的验证。 |
| Group add/update 提交后头像读失败未测试 | 属实，补两个确定性注入测试，同时断言 Internal、实际头像读取、变更已持久化，以及故障解除后的读取/更新行为。 |
| Human 不会有本地记录文件 | 假设不成立：`ensure_human_actor` 后调用普通 `save_to_storage` 就会生成文件，原 batch read 会返回该 Human。共享 Memory/SQLite 契约补此场景；memory 在读取文件前排除 Human，重启/删除后的旧文件复用现有 Agent Bot 磁盘扫描的 `human_` 保留命名空间规则。活 registry 的 actor_kind 优先。另测损坏 Human 遗留文件不会污染 Bot 展示读取。 |
| memory identity 文件称 verbatim | 修正文档，说明抽取后增加了共享记录锁和原子写入。 |
| `tempdir().keep()` 泄漏目录 | 四个 avatar helper 改为返回 TempDir，由测试或 fixture 持有；Group validation 中另一处 keep 同样去掉，测试结束自动清理。 |
| Group 摘要未防 detail 字段泄漏 | 补 `role/mode/tags` 缺席断言，保留已有字段存在性和 `message_view_scope` 缺席断言。 |
| replay token 的 `is_null()` 有歧义 | 依据 DTO 的 skip-none 契约，改为断言 `bot_runtime_token` **键缺席**。 |
| URL 长度/格式验证 | 本轮不增加约束。design §4.2 已明确保持已有来源及校验规则兼容，不探测/下载 URL。64 KiB 测试值用于验证并发完整文件读取，不作为新增 API 长度承诺。 |
| 文件大小 waiver 将到期 | 截至 2026-11-03 尚未到期（2026-11-10）；保留 §6.5 跟踪，不在本轮重构无关的大文件或续期 waiver。 |

Human 缺陷修复前：`memory_avatar_contract`、重启/删除读、损坏 Human 文件读三个测试失败，分别暴露错误成员返回及非法 JSON 错误。修复后继续运行相同契约，保留普通 Bot tombstone/旧文件头像读取语义。

本轮验证（未提交工作区）：

| 检查 | 结果 |
| --- | --- |
| `cargo check --workspace --all-targets --locked` | exit 0；service-api 2.0.0 的所有 workspace 构造点编译通过 |
| `cargo nextest run -p bcs-service-api -p bcs-bot -p bcs-app-group -p bcs-app-session --locked --no-fail-fast --retries 0 --test-threads 8` | 952/952 passed，0 skipped、无重试 |
| `cargo test -p bcs-app-group -p bcs-app-session --test v1_group_service --test v1_session_service avatar` | Group 11、Session 9 passed；隔离 TMPDIR 运行后残留条目为 0 |
| `cargo test -p bcs-http --test avatar_write_contract` | 8 passed |
| `cargo test -p bcs-bot-store --locked --test conformance_bot_profile --test bot_avatar_queries --test memory_profile_concurrency` | 111 passed（56 + 49 + 6），包含最终版本的新 Human 读取用例 |
| `cargo metadata --format-version 1 --offline` | service-api 解析为 2.0.0，65 个直接 consumer；lockfile 仅更新该本地包版本 |
| `BCS_BASE_REF=a9b62a1f27 bash scripts/ci/check-public-api.sh` | exit 77，缺少 cargo-public-api，未执行完整公开 API diff |
| `check-store-boundaries.sh` / `check-forbidden-symbols.sh` | exit 0 |

独立复审未发现新增逻辑问题；10 个修改的 Rust 源文件均不超过 1,000 行（最大 795），`git diff --check` 通过。本轮未修改或弱化 CI gate，未重跑真实 MySQL 或完整 Singlebox 栈。

## 11. PR 3488 行级评论修复：资料写保留运行时身份（2026-11-03）

评审人 @carolynli 的 P1 评论（`persistent_repo/profile.rs` 行级，提交 `e930d2fbc8`）经代码链逐项核实**属实**：admin 资料更新的合并 capabilities 不带身份（`application/onboarding.rs` 以 `..Default::default()` 构造），Core 的 Provider 保护只回填 Provider 关联 Bot 的 `agent_code`（不含普通 WS Bot，且不含 `agent_token`），两种存储随后整体发布这份无身份快照——memory `existing.capabilities = update.capabilities.clone()`、persistent `publish_profile_capabilities` 同样的整体替换，且持久层 UPDATE 还把 `agent_code` 列与 bot_info 的 `$.agent_code`/`$.agent_token` 键写成 NULL/JSON null。普通在线 WS Bot 在一次 admin 改名（即使不带头像）后读到 `(None, None)`；`get_agent_credentials` 读的就是这份内存值，`group_flow/interceptors.rs` 对无 `agent_code` 的一方直接跳过整条出站拦截器链（只记 warn），出站安全策略被静默绕过，直到该 Bot 下次携带身份重注册。旧 `save_to_storage` 两种存储均在传入为 `None` 时保留内存既有身份，确属本 PR 引入的回归。

复现先于实现：在共享契约 `tests/support/bot_profile_contract.rs` 新增"身份保留"用例，按评审场景构造真实 WS 在线姿态（`register` 携带 agent_code 注水活 entry，再 `add_bot_info` 投递运行时 token），然后发起无身份的资料写——修复前 Memory 与真实 SQLite 两个挂载点的 `get_agent_credentials` 均返回 `(None, None)`（断言失败），修复后同一用例全绿。该契约同时由 Memory、真实 SQLite 迁移链挂载点执行，也被 bcs-admin 真实 MySQL 链路门禁挂载（本轮本机无 MySQL 实例，门禁用例标 `#[ignore]`，未跑）。

修复（按评论建议落在存储层，不从脱敏 `RegisteredBot` 回填 token，不把运行时 token 写入数据库）：

- persistent：资料 UPDATE 改用 profile-only 列投影 `project_profile_columns`（`agent_code` 列与两个身份 JSON 键不再出现在 SET 子句，注册/保存路径保留完整投影不动）；`publish_profile_capabilities` 改为逐字段发布资料字段、保留活 entry 的 `agent_code`/`agent_token`。
- memory：`update_profile_existing_impl` 的记录构造保留当前身份（活 entry 优先、文件兜底，与 owner 保留同优先级），内存发布同样逐字段保留身份。
- service-api：`ExistingProfileUpdate` 与 `BotRepoPort::update_profile_existing` 契约文档明确"资料写永不写运行时注册身份，实现必须保留"（纯文档，API 面已被前一轮 2.0.0 覆盖，无新变更）。

本轮验证（命令在 `apps/bcs` 下运行）：

| 检查 | 结果 |
| --- | --- |
| 修复前复现：`cargo test -p bcs-bot-store --test conformance_bot_profile`（暂存存储实现改动） | `memory_avatar_contract` 与 `sqlite_avatar_contract` 均失败于身份断言（`left: None`），复现评论描述 |
| 修复后同一测试 | 56 passed |
| `cargo test -p bcs-bot-store` | 全部 profile/avatar/token 套件通过；仅剩 §6.7/§9 登记的两例 control-plane 既有失败（已在未修改 HEAD 上复跑确认同样失败） |
| `cargo test -p bcs-bot`、`cargo test -p bcs-service-api` | 全部通过 |
| `cargo test -p bcs-http --test avatar_write_contract` | 8 passed |
| `cargo test -p bcs-app-group --test v1_group_service`、`cargo test -p bcs-app-session --test v1_session_service` | 102 / 88 passed |
| `cargo check --workspace --all-targets` | exit 0；仅保留无关 crate 的既有 warning |

改动文件均不超过 1,000 行（最大 470）。真实 MySQL 与完整 Singlebox 栈未重跑；新增断言位于共享契约，MySQL 链路门禁跑通时会自动覆盖同一语义。本轮未修改 CI gate。
