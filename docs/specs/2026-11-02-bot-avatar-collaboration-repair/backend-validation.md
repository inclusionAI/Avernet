# Backend Bot 头像同步与手动修复验证与交付记录（Task 9）

- 日期：2026-11-03 至 2026-11-04（实测运行跨日）。
- 分支：`feat/bot-avatar-backend`（未推送、无远端跟踪）。Task 9 开始时基线 =
  `feat(backend): expose owner-scoped collaboration repair endpoint`（原 SHA
  `2e89543c31`；本 Task 全量回归暴露的两处 Task 8 交付缺口按 §1.1 修复后本地
  amend 为 `0029a8088c8935dced8736d4f9aa36021025ae37`，无后续提交需要改写）+ 干净树。
- 覆盖的提交链（依靠的 BCS 与 Backend 现状）：`29352dcd17`…`f58dbaf69f`（BCS 侧，Task 1–8 + 复审修复），`29184e2c48`…`2e89543c31`（Backend 侧，Task 1–8 原提交链；`2e89543c31` 的最终形态见 §1.1 的 amend 说明）。
- 交付形态：Backend 侧的头像同步与单次手动修复接口（`POST /openapi/v1/bots/{bot_id}/collaboration/repair`）全部实现，并由本文件的实测记录验证；前端按钮不在本 plan 范围。
- 关联契约：[bcs-validation.md](bcs-validation.md)（BCS schema/接口版本），[design.md](design.md) §6–11。

## 0. 本文件引用的实现版本

- Backend 提交：本 Task 9 提交 `test(backend): verify avatar sync and manual collaboration repair`（本文件所在提交；所有实测运行在其内容的工作树上执行——见 §1 首段的被测树表述）。
- BCS 提交：本分支链上的 `e930d2fbc82a8a18144be0a574606099e2757e3b`（`fix(bcs): exclude human avatars and version the service API`，即本 plan BCS 侧交付单提交的完成后状态；[bcs-validation.md](bcs-validation.md) §1–§5 是其验证）。
- Relied-upon BCS 契约版本（与 [bcs-validation.md](bcs-validation.md) 一致）：
  - Schema：SQLite/MySQL migration 链至 `040_bot_avatar`（checksum
    `d4fcf858…9e3b715e5`），`bcs_bots.avatar_url TEXT NULL`，无回填、无索引。
  - 写路径：`POST /admin/bots/onboard` 的 Keep/Clear/Set 语义；existing-only
    （缺失目标 `onboarded=false` 零写入，§5.4 replay 已在本次联调复放）。
  - 读路径：V1 Group/Session list+get 输出参与者 `avatar_url`（当前展示资料，
    null 显式存在；legacy 面/WS 不新增字段）。
  - service-api 版本 2.0.0（[bcs-validation.md](bcs-validation.md) §8）。

## 1. 实测结果（命令、退出码与关键数字）

被测树：本 plan 最终提交系列的内容（Task 1–8 系列 + 本 Task 9 提交）。所有实测
运行在提交前的工作树上进行，其内容与最终提交树**逐字节一致**（各命令运行时间点
只存在尚未拆分的提交归属差异，无内容差异）。

| 命令 | 退出码 | 关键数字 |
| --- | --- | --- |
| `pt tests/community/core/bot_management/services tests/community/core/bot_management/collaboration_repair tests/community/core/bot_collaborator/manager_sync tests/community/contracts/test_bcs_collaboration_metadata.py tests/community/contracts/test_bcs_ownership.py tests/community/contracts/test_bcs_managers.py tests/community/architecture -q`（`pt` 即 `(cd apps/backend && DEPLOY_PROFILE=test SERVER_ENV=dev uv run --frozen pytest ...)`，最终代码状态复跑两次） | 0 | **1780 passed / 0 failed**（61.40s） |
| `bash apps/backend/scripts/ci_test.sh --base e930d2fbc82a8a18144be0a574606099e2757e3b --head HEAD` | 0 | **backend CI gate passed**；**29080 passed、40 skipped（29126 用例 100.00%）**；行覆盖率 **90.91%**（≥75）；变更行覆盖率 **87.16%（2695/3092，≥80）** |
| `bash singlebox/ci/singlebox_coverage.sh --module collaboration_repair`（focused 阈值确认跑） | 0 | **singlebox coverage gate passed**；acceptance **4 passed**；`collaboration_repair coverage: core=36.36% router=100.00% plugin=0.00%`；BCS E2E line **52.67%**（≥41）、method **46.90%**（≥36）、endpoint **173/173=100%**、bcs-cli **66/66=100%**、changed-line **61.12%（4930/8066，>60%）**（runner 默认 `origin/dev` 基线，含 target-only 污染仍达标） |
| `bash singlebox/ci/singlebox_coverage.sh`（默认全部模块，全新 `.dependencies` 状态） | 0 | **`singlebox coverage gate passed`**；Backend acceptance **69 passed / 0 failed**（456.19s，含本模块 4 story）；**14 个模块**非回归全部达标（数字见 §3.3）；BCS E2E line **53.16%**（≥41）、method **47.12%**（≥36）、endpoint **173/173**、bcs-cli **66/66**、changed-line **61.27%（4942/8066，>60%）**（runner 默认 `origin/dev` 基线，含 target-only 污染仍达标） |
| `python singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/singlebox/reports` | 0 | **`singlebox coverage artifacts verified`**；backend 49.51%（≥38）、BaaS 45.94%（≥45）、BCS runtime line/method/router/cli 53.16%/47.12%/100.00%/100.00%、backend router 命中 1082/plugin 证据 9、BaaS router 命中 294 |

### 1.1 首次全量回归发现并修复的既有缺陷

`ci_test.sh` 首跑暴露两个 **Task 1–8 范围遗留、与本 Task 9 新增验收无关**的缺陷。
两者都是 Task 8 交付物的自身缺口（split 文件丢失 re-export、其新增路由缺少
per-endpoint 覆盖用例），因此**并入 Task 8 的对应提交**（该提交是分支 tip、
从未推送，amend 不改写任何后续提交）而非本 Task 9 提交；本 Task 9 提交因此
只含 Task 9 计划文件（验收套件、validation 文档、registry、README/运维文档）：

1. **`responses.py` 拆分丢失 re-export**（Task 8 引入）：`test_task_callback_report.py`
   的 10 个用例 `ImportError: cannot import name 'CODE_OK' from
   .../openapi_v1/responses`。修复：恢复从 `contracts` 导入
   `CODE_ACCEPTED/CODE_CREATED/CODE_OK/Deleted/Page` 并加入 `__all__`
   （与模块 docstring 的 re-export 承诺一致）。修复后该文件 **40 passed**。
   该遗漏仅影响全量后端测试门禁（聚焦门禁在同 worktree 从未全量跑过）。
2. **端点覆盖门禁缺 repair 路由用例**（Task 8 引入）：
   `tests/community/framework/test_coverage_gate.py::test_endpoint_coverage_against_baseline`
   报 `1 NEW uncovered endpoint(s): POST /openapi/v1/bots/{bot_id}/collaboration/repair:
   missing [happy, error]`。修复：新增
   `tests/community/endpoints/test_collaboration_repair_endpoint.py`
   （happy / forbidden_user_scope / write_payload_refused 三例，经 per-endpoint DI
   框架驱动真实路由）。门禁转绿。

## 2. 真实 Stack 联调（live stories）

环境：`singlebox/ci/singlebox_coverage.sh` 提供的共享单盒栈（backend 8888 / baas 8890 /
BCS 21000，`DEPLOY_PROFILE=singlebox`、`BCS_AUTH_MOCK=1`，backend 与 BCS 共享
`singlebox-gateway-principal-key-not-for-production` 签名 key 与真实写入）。
新验收套件 `tests/community/acceptance/collaboration_repair/`（`conftest.py` 生命周期、
`live_case.py` helper、`test_collaboration_repair_live.py` 四个 story）
只在该共享栈上运行（`SINGLEBOX_ACCEPTANCE_REUSE_LIVE=1` 门禁），fixture 数据
全部带 `colabrepacc` 前缀 + 每 case 唯一后缀，不触碰测试之外的数据。

### 2.1 领域说明：本栈可证边界（勿当 cover-up 阅读）

单盒公开本地栈（`application-singlebox.yaml`：`bcn` 块仅有 `base_url`，
"**No provider creds: singlebox never registers**"）**没有 BCN Provider 凭据**；
`BcnService.get_attributes` / `sync_bot_caller_config` /
`register_provider_bot` 在 dev 无凭据时按设计返回 `{"skipped": True}`。
因此：

- 修复接口的 `exists` 探测与 caller/资料注册通道在该栈**不可能成功执行**——
  `CollaborationRepairModule` 在 DI 装配时即判定
  `provider-scope-unavailable`，`factory.open` 在构造任何 Plugin 之前拒绝，
  按文档映射为 **HTTP 503 `Collaboration repair is unavailable`**。
  这是 Task 5/7 设计并经 Task 7 单测覆盖的部署前置条件
  （`test_collaboration_repair_module.py`："disabled 配置时工厂产出明确 unavailable"）。
- 生产环境（corp overlay，pre/prod 有 provider 凭据与 BCS
  `allowed_switch_provider_ids` 配置）才具备 200-report 执行条件；分支的
  service API / metadata Plugin adapter / caller 映射由单测、契约与路由套件钉住
  （`test_collaboration_repair.py` 的 404/409/502/504 前置映射与四项报告 schema）。

### 2.2 Story 1：注册头像 → 日常编辑/清空 → V1 读回（全真实链路）

`test_avatar_registration_daily_sync_and_v1_readback` — 证明内容：BCS
登记时保存的显式头像、Backend 真实 owner 路由（`PUT /openapi/v1/bots/{bot_id}/avatar`，
`update_bot(ext={"avatar_url": …})` → `_sync_bot_to_bcn` → 真实
`POST /admin/bots/onboard`）把已提交 Backend 事实送达 BCS，
且 **V1 Group 与 Session 的 list+get 四个读回面**恒等于当前头像；
显式 `""` 清空在读回面是 `null`（键保留），而非旧值回退。

实测证据（全量运行 `singlebox/.dependencies/logs/backend.log` 的真实行；
`<bot>:<owner>` 为该 case 唯一 fixture 复合 ID，V1/sqlite 断言即 story 断言本身）：

```text
# fixture 注册（真实 BCS admin 通道，T8 §5.4 replay）：
POST /admin/bots/onboard {"bot_id":"colabrepaccbot_<suffix>:colabrepaccown<suffix>", ...,
                          "avatar_url":"https://cdn.example.test/colabrep/registered.png"}
    -> {"onboarded":true,"bot_uuid":"colabrepaccbot_<suffix>:colabrepaccown<suffix>", …}
SELECT name, avatar_url FROM bcs_bots WHERE bot_uuid=…
    -> ("Story bot colabrepaccbot_<suffix>", "https://cdn.example.test/colabrep/registered.png")

# Backend 真实编辑（owner principal，PUT avatar ×3：set → ""→Null → set）；
# 每次 PUT 均真实落确认日志（backend.log 实录，三种行各出现 ×3）：
[BcnService.onboard_bot] Calling BCN onboard: bot_id=<bot>:<owner>, …
[BcnService.onboard_bot] BCN onboard succeeded: bot_id=<bot>:<owner>, bot_uuid=<bot>:<owner>, onboarded=True
[bot_service._sync_bot_to_bcn] Synced to BCN: bot_id=<bot>:<owner>, …
# 之后四个真实 V1 读回：两个 LIST 面用 view_bot_id=<driver> 作为观看者
# （driver 的 created_by 即该 owner；list 按观看者成员关系过滤），两个
# DETAIL 面直接以 owner-creator 身份鉴权（不需要 view actor）：
GET /openapi/v1/collaboration/groups                 -> items[].participants[actor_id=<bot>:<owner>].avatar_url
GET /openapi/v1/collaboration/groups/{gid}           -> participants[].avatar_url
GET /openapi/v1/collaboration/groups/{gid}/sessions  -> items[].participants[].avatar_url
GET /openapi/v1/collaboration/sessions/{sid}         -> participants[].avatar_url

# 注册段 == registered.png；set 后 == first.png；清空 "" 后 == null（键保留，
# sqlite avatar_url 同步为 NULL）；再次 set 后 == second.png
# 全程 bcs_bots 行数不变（复用现有行；existing-only，无隐式建行）
```

未出现 `skipped=True`/`onboarded=false` 的成功误读——`_sync_bot_to_bcn` 的非成功
路径只告警，onboard 均为真实 2xx 确认（`onboarded=True` + 复合 ID `bot_uuid` 回显）。

### 2.3 Story 2：已存在目标 + 已制造漂移 → 能力门拒绝 → 无任何写入

`test_repair_refuses_at_capability_gate_and_writes_nothing` — 证明内容：
对**已存在**的 BCS Bot（fixture 用官方 admin onboard 制造 name+avatar 漂移），
owner 通过真实路由调用修复，得到文档化的 503 能力门拒绝，且
**漂移仍然原样保留**（无部分写入、无后台静默补偿、无入队重试）。

实测证据：

```text
POST /openapi/v1/bots/<bot_id>/collaboration/repair?user_id=<owner>   (X-Avernet-Principal: owner)
    -> HTTP 503 {"code":503000,"message":"Collaboration repair is unavailable","data":null}
backend.log: "[COLLABORATION-REPAIR] unavailable: reason=provider-scope-unavailable"
backend.log: 无任何 "[COLLABORATION-REPAIR] bot_id=… status=…" 分项行（未进入执行）
SELECT COUNT(*) FROM ac_task_queue WHERE task_type='collaboration_repair'  -> 0
BCS：ownership 读回 == 调用前；managers 读回 == 调用前；
     bcs_bots 行数 == 调用前；name/avatar 漂移原样保留
```

### 2.4 Story 3：BCS 目标缺失 → 拒绝且零创建

`test_repair_missing_bcs_target_never_creates` — 证明内容：Backend 行存在、
BCS 对应 Bot **从未注册**（不调用注册让断言通过的前提下），修复调用确认：

```text
GET /bots/<bot>:<owner>   -> 404 {"code":"bot_not_found"}
POST …/collaboration/repair?user_id=<owner> -> HTTP 503（能力门,data=null）
   * 调用前后 bcs_bots COUNT 完全一致，目标 bot_uuid 计数 0（零创建证明）
   * 调用后 GET /bots/<bot>:<owner> 仍 404 bot_not_found
   * 后端无 repair 队列行；日志仅有 unavailable 行，无分项执行行
```

带 404 `collaboration_bot_not_found` 的目标缺失映射本身（HTTP 200 + report
尾 Familial 例外之外的前置 404）由路由/服务单测与契约钉住：
`test_collaboration_repair.py::test_service_preconditions_map_to_their_documented_status`
（404/409/502/504 全等级映射）与 `test_collaboration_repair_module.py` 的
unavailable 工厂测试一致；本栈 live 层证明零创建与能力门前置不可绕过。

### 2.5 Story 4：非 owner 掩蔽 404

`test_repair_non_owner_never_reaches_the_interface`：另一已注册真实 principal
调用同一 bot 的修复路由 → 404（data null），日志无任何 `[COLLABORATION-REPAIR]` 行
（未进入 repair 模块）。
真实权限链（owner-scoped admission + `get_bot(bot_id, caller)`）拒绝非 owner，
与所有权交接后的旧 Owner 拒绝语义一致（design §7.1）。

## 3. Singlebox coverage 注册与阈值（实测）

### 3.1 registry 项

`singlebox/ci/singlebox_coverage_modules.yaml` 新增 backend 模块
`collaboration_repair`（BCS 模块条目未触碰）：

- `acceptance_targets: tests/community/acceptance/collaboration_repair`
- Core 分母：整个 `src/agentclaw/community/core/bot_management/collaboration_repair/`
  （facts 投影 + Service API 契约 + 请求内编排；`avatar_from_record`
  /`outbound_avatar_from_record` 在真实每日编辑链路中运行）。
- Router 分母：唯一 POST
  `POST /openapi/v1/bots/{bot_id}/collaboration/repair`。
- Plugin 分母：`BcsCollaborationMetadataPlugin` 的
  `exists/update_profile/sync_caller/aclose`；
  manifest 证据门要求证据位于 `src/agentclaw/community/plugins` 之下，故指向
  该 Plugin 的插件树实现
  `plugins/local/bcs_collaboration_metadata.py::RecordingBcsCollaborationMetadataPlugin`
  （计划文件地图内的 recording local 实现）；请求侧生产装配（组合根）实现位于
  `di/bcs_collaboration_metadata_adapter.py`（组合 glue，保持 ownership_sync
  条目模型——生产 Owner/managers Http Plugin 的分母不变、旧阈值不动）。

### 3.2 focused 实测与阈值

三次 focused `--module collaboration_repair` 运行的测量历史（每次全部记录，未挑选数字）：

| 运行 | 运行前提 | 结果 |
| --- | --- | --- |
| 第 1 跑（测分母） | 既有 `.dependencies` 运行态（本 worktree 首跑） | acceptance **2 failed, 2 passed**：`test_avatar_registration_daily_sync_and_v1_readback` 因 V1 Group list 未选 view-actor 失败；`test_repair_non_owner_never_reaches_the_interface` 因本测试自身的元组比较笔误失败。两个 repair 边界 story 全绿。**core=34.93% router=100.00% plugin=0.00%**，BCS E2E 全绿（endpoint 173/173） |
| 第 2 跑 | 修复两个 story 后，共享 `.dependencies` 运行态 | acceptance **4 passed**；`collaboration_repair coverage: core=36.36% router=100.00% plugin=0.00%`；BCS E2E 失败于 **[bcs-validation.md](bcs-validation.md) §7 已登记的时间性 flake**（`story_user_operates_group_workspace` 在 persistent group message 步骤 400 `queue_draining` 早退 → endpoint 171/173；T8 记录同一二进制第二跑全绿）。据此按该文档结论清理 `.dependencies` 一次性运行态后重跑 |
| 第 3 跑（阈值确认） | **清空 `singlebox/.dependencies`（git-ignored 一次性运行态，CI 等价条件）后的全新栈** | **exit 0：`singlebox coverage gate passed`**；acceptance **4 passed**；`collaboration_repair coverage: **core=36.36%** router=100.00% plugin=0.00%`；BCS E2E line 52.67%、method 46.90%、endpoint **173/173**、cli **66/66**、changed-line **61.12%（4930/8066）**；`summary.json` 模块 core 计数 **76/209** |

据此设置的阈值（全部取自本模块实测，低于各绿跑实测值以留出运行方差；未复制任何其他模块数字，
未触碰既有模块阈值）：

| 指标 | 实测（阈值确认跑） | 设定阈值 |
| --- | --- | --- |
| Router API（`POST /openapi/v1/bots/{bot_id}/collaboration/repair`） | 100.00%（1/1） | 100.0 |
| Core Line（整个 `core/bot_management/collaboration_repair/`，209 语句） | 36.36%（76/209） | 36.0 |
| Plugin API（4 项 metadata 方法） | 0.00%（0/4） | 0.0 |

- Router：两个 repair story（owner 503 调用 + missing-target 调用）+ 非 owner 掩蔽
  404 调用真实击中该唯一路由，middleware 记录 1/1。
- Core：`profile.py`（头像素投影）在真实每日编辑/注册链路执行；
  `service.py` 的四项编排、facts reader 与 adapter 的方法体在能力门拒绝下**不会执行**
  ——实测数字如实记录（72/209 未覆盖由前置 503 决定，非省略函数抬高）。
- Plugin：无凭据栈不绑定该插件树实现（recording 实现仅在 TEST 列装配），实测 0/4、
  阈值 0.0（不是覆盖造高点，是单盒栈能力边界；见 §2.1 与 §7 追踪项）。

### 3.3 默认全部模块运行

`bash singlebox/ci/singlebox_coverage.sh`（无 `--module`，全新 `singlebox/.dependencies`
后运行）：**exit 0，`singlebox coverage gate passed`**；`coverage_modules` 全部
**14** 个条目（既有 13 + 新增 `collaboration_repair`）非回归通过，阈值错误列表为空：

| 模块 | core 实测（旧阈值） |
| --- | --- |
| bot_dormant | 56.65%（≥54.13） |
| tc_file_upload_integrations | 80.13%（router/plugin 100%） |
| auth | 100.00% |
| devices | 49.39%（≥44.44） |
| access | 45.42%（≥42.80） |
| bot_chat | 69.37%（≥67.48） |
| bot_collaborator | 65.04%（≥53.88） |
| manager_sync | 70.03%（≥54.03） |
| ownership_sync | 65.91%（≥65.90；plugin 100%） |
| **collaboration_repair（新增）** | **36.36%（≥36.0；router 100%；plugin 0（≥0））** |
| cron | 44.91%（≥41.84） |
| expert_chat | 62.14%（≥61.18） |
| files | 69.39%（≥63.52） |
| harness | 50.75%（≥40.86） |

既有模块阈值未改动、未弱化；BCS E2E 汇总：line 53.16%（≥41）、method 47.12%
（≥36）、endpoint **173/173=100%**、bcs-cli **66/66=100%**、changed-line
**61.27%（4942/8066，>60%）**。随后对同一 reports 目录执行
`verify_singlebox_coverage_artifacts.py`：**exit 0**，
`singlebox coverage artifacts verified`（backend 49.51% ≥38、BaaS 45.94% ≥45、
BCS router/cli 100%/100%、backend router 命中 1082/plugin 证据 9）。

多模块共享栈下的相互干扰检查（AGENTS.md 要求）：默认全部模块的同一栈上，本模块
story 与 owner/managers/dormant/expert_chat 等全部 story 并存运行，
collaboration_repair 双重复测（focused 36.36% / 全量 36.36%）一致，
无共享栈干扰回退。

## 4. 单次执行语义与已知局限（评审交接快照）

1. **无自动重试**：修复不产生任何队列/重试/补偿状态。Live 证据：调用前后
   `ac_task_queue` 无 repair 任务、日志无分项行；服务/单测固定顺序见
   `collaboration_repair/service.py` 的不变循环结构。
2. **超时/取消局限**：deadline 在发送前耗尽 → 该项 `failed`/`delivery_timeout`；
   在途预算耗尽 → 该项 `unknown`（远端结果未确认，不承诺未修改）；
   同步线程中的取消不能强停底层发送；已关闭的请求侧 client 不跨事件循环复用
   （Task 7 分离测试覆盖）。这些都是拒绝被美化的边界，未在 live 栈制造
   （provider 凭据缺失使探测即失败）。
3. **`BcnService` 无公开 timeout 访问器**：组合根读取 `_timeout`（30s）作为
   单次修复预算（`collaboration_repair_module.py` 内有注释说明），测试用 pin
   断言；后续清理候选（见 §7）。
4. **BCN 上游错误体截断**：借用的 `bcn_service.py` 在错误日志中截断上游 body
   （既有行为，非本 plan 引入）：known limitation，未在本轮修改。
5. **`metadata.exists` 需 provider 凭据**：无凭据环境的修复探测按设计 503
   （§2.1）；生产部署需配置 `bcn.provider_id/provider_admin_token`（community
   overlay 从 env 注入）与 BCS 侧 Provider/attributes allow-list。

## 5. 交接与发布顺序

1. **先 BCS 后 Backend**（不可逆序）：BCS 先上线 040 迁移 + 独立列
   `avatar_url` 与 existing-only 行为；Backend 的头像同步与修复路由后上线。
2. **回滚规则**：回滚 Backend 不删 BCS 列（040 迁移与 nullable 字段保留），
   不撤销既有已成功修复的 Owner 变更；BCS 应用回滚不导出头像数据。
   旧 BCS 环境的 2xx 忽略新字段不构成本 plan 的成功验收证据（design §10.1）。
3. **未升级 BCS 环境下的 Backend 新路由**：写路径凭据存在与否均可进入路由：
   - 无 provider 凭据：503 能力门拒绝（本提交 live 证明零写入）；
   - 有凭据但 BCS 未升级（忽略 `avatar_url`/`onboarded` 语义不符）：
     不得将 2xx 当头像同步成功，运维按 §2.1 判断部署顺序。
4. **前端**：后续按 `report.status`（`succeeded`/`partial_failed`/`failed`）
   展示成功/部分失败，HTTP 200 不等于全部成功；本 plan 不实现按钮。

## 6. 文件与行数

| 文件 | 行数 |
| --- | --- |
| `apps/backend/tests/community/acceptance/collaboration_repair/conftest.py` | 26 |
| `apps/backend/tests/community/acceptance/collaboration_repair/live_case.py` | 374 |
| `apps/backend/tests/community/acceptance/collaboration_repair/test_collaboration_repair_live.py` | 137 |
| `apps/backend/tests/community/endpoints/test_collaboration_repair_endpoint.py` | 181 |
| `docs/specs/2026-11-02-bot-avatar-collaboration-repair/backend-validation.md` | 350 |
| `apps/backend/src/agentclaw/community/core/bot_management/collaboration_repair/README.md` | 234 |
| `apps/backend/docs/bot-ownership-sync-operations.md` | 264 |
| `singlebox/ci/singlebox_coverage_modules.yaml`（新增模块段） | 431 |
| `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`（re-export 修复） | 981 |

全部 ≤1,000 行；`git diff --check` 通过；新增/修改行无密钥、无数据库迁移、
无 runtime state、无私有 endpoint、无新增依赖（pyproject 未提交变更）。

## 7. 待办追踪（非本 Task 修复）

1. （§4.3）为 `BcnService` 暴露公开 timeout 访问器并移除组合根的 `_timeout` 读取。
2. （Task 7 评审遗留）将 `CollaborationRepairPlugins` port 拆分至独立 di 模块。
3. （单盒联调覆盖）在共享栈配置 provider scope（BCS 本地
   `allowed_switch_provider_ids` + 栈级 provider 注册 + backend 侧
   provider 凭据注入，或以 DEPLOY_PROFILE=corp overlay 联调）后，把
   `collaboration_repair` 模块（core/plugin 阈值）按 §3.2 的说明重测并上调。

## 8. 未执行项与原因（不以 mock/编造数字替代）

1. **Live 双侧的 200 修复报告与四项分项读回（ownership/profile/managers/caller_config）**：
   单盒公开本地栈无 BCN provider 凭据（§2.1），修复请求在 `factory.open` 的
   能力门处即按设计 503，探测与四项写入在 live 栈不可能执行；其接口契约、
   语义检查与 HTTP 200/报告 schema 由下述已绿门禁钉住而非跳过：
   `tests/community/adapters/http/openapi_v1/test_collaboration_repair.py`
   （四项顺序、partial_failed/unknown 分项、404/409/502/504 前置映射、
   `GET /bcs/repair` 旧路径 404）、
   `tests/community/di/test_collaboration_repair_module.py`
   （请求 loop 分离、aclose、unavailable 工厂）、
   `tests/community/contracts/test_bcs_collaboration_metadata.py`（真实 SQL
   facts + `CollaborationRepairService` + recording Plugin 的消费端到端，含
   `succeeded` 四项报告与 missing 零调用）、以及 §3.2/§3.3 全量单后端门禁。
   上线后第一次在带 provider scope 的环境（corp pre）执行时应回补 live 双侧
   四项读回记录。
2. **Backend 创建路径的 provider 注册携带头像（create_bot →
   `_register_bot_to_bcn_as_provider(avatar_url=…)`）的 live 证据**：
   单盒 dev 无凭据按设计 skip（`_get_provider_config` 见 §2.1）。该路径的
   payload/激活行为由单测钉住：
   `tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py`
   与 `test_bcn_service_register_provider.py`（省略/设置/清空字段的 wire 语义）；
   BCS 侧的消费由 [bcs-validation.md](bcs-validation.md) §2 实测（真实 MySQL 链路
   完整绿）。本 Task 的 live 注册段使用 BCS 官方 admin onboard 通道复放认证
   （T8 §5.4 步骤 3），头像写入语义同一实现。
3. **caller_config 的 live CAS（stale/unchanged/applied）**：同因 §2.1（calls
   `sync_bot_caller_config` 需 provider 凭据）；BCS 侧交互由
   [bcs-validation.md](bcs-validation.md) 所引 e2e `caller_identity_story` 实测，
   Backend 侧由 `test_bcn_service_sync_caller_config.py` 与适配器单测钉住。
   本 live 故事集里 caller_config 一律 `not_applicable`（无可建立的 caller 配置，
   未在 fixture 中伪造）。
