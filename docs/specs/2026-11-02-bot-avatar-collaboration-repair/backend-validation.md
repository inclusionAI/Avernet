# Backend Bot 头像同步与手动修复验证与交付记录（Task 9）

## PR #3569 复审修复（2026-11-04）

当前分支为 `feat/bot-avatar-backend-squashed`，复审基线 `8cc26f9`。
本节更新后文历史记录；实现与验证计划见 [backend-review-fixes.md](backend-review-fixes.md)。

- 修复三个边界：事实读取受整体 deadline 约束；managers 已分类读取/投影失败
  保留 ownership/profile 的执行结果；managers 确认目标缺失后停止 caller 写入。
- 新增回归先红后绿；真实 managers HTTP Plugin 的 404、实际 Service 到 HTTP
  200 部分报告、取消后的迟到数据库结果均有断言。读取自身抛出的未分类
  TimeoutError 保持传播，不误报请求截止时间耗尽。
- Backend 全量门禁最终工作树实测：`BACKEND_CI_SKIP_INSTALL=1
  BACKEND_CI_PYTEST_WORKERS=6 bash apps/backend/scripts/ci_test.sh`：**29,591 passed /
  40 skipped / 6 subtests passed**，行覆盖率 **90.99%**，变更行覆盖
  **87.26%（2720/3117，≥80）**。最终提交后 pre-push/远端 CI 会以提交内容为
  边界重跑同一门禁。
- 原失败 CI 的两个测试有独立调度故障复现：逐实例解析延迟 0/50/100ms
  重现文件计数最大并发 1；LLM 额外延迟 600ms 重现 fencing 场景 reschedule。
  修复使用事件协调并发、为 fencing 完成流程提供独立 slice 预算，保留所有
  行为断言。延迟复现 **2 passed**，相关扩展套件 **89 passed**。
- 首次 focused Singlebox 的四个现有场景通过，但模块 Core 32.44% 未达
  36% 门槛；BCS 旧目录残留前一天未结算队列，正确触发 queue_draining，
  导致两个后续端点未执行。保留该失败记录；后续使用新的运行目录和真实
  Provider 验收装配，不调整覆盖率阈值。
- Rebase 到 `origin/dev f555d126` 后保留原拆分并吸收 dev 增量：metadata
  pair resolution 入 `bot_service_parts/queries.py`，ownership/manager facts 全链
  补齐必填 `bot_type`，HTTP allowlist 与 operation placement 计数同时包含
  catalog/metadata 与 repair。首轮全量门禁暴露 42 个旧构造签名错误并已全部
  修复；首轮单盒复跑到 harness 时本地 ARCA 18800-18899 端口耗尽（BaaS 明确
  `No free port available`），清理 60 个本 worktree 残留沙箱进程后全新复跑转绿，
  未把环境问题计入代码失败。

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
| `BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=6 bash apps/backend/scripts/ci_test.sh`（最终复审修复树） | 0 | **backend CI gate passed**；**29,591 passed、40 skipped、6 subtests passed**；行覆盖率 **90.99%**（≥75）；变更行覆盖率 **87.26%（2720/3117，≥80）** |
| `bash singlebox/ci/singlebox_coverage.sh --module collaboration_repair --coverage-root singlebox/.dependencies/coverage/pr3569-focused-v3`（final focused） | 0 | **singlebox coverage gate passed**；acceptance **5 passed**；`collaboration_repair coverage: core=71.11% router=100.00% plugin=100.00%`；BCS E2E coverage gate passed、endpoint **173/173=100%**、bcs-cli **66/66=100%**；临时 Provider 凭据已清理 |
| `bash singlebox/ci/singlebox_coverage.sh --coverage-root singlebox/.dependencies/coverage/pr3569-final-rebased-v2`（rebase 后最终默认全部模块） | 0 | **`singlebox coverage gate passed`**；Backend acceptance **71 passed / 0 failed**（547.96s，含本模块 5 story）；**14 个模块**非回归全部达标（数字见 §3.3）；BCS E2E coverage gate passed、endpoint **173/173**、bcs-cli **66/66** |
| `python singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/pr3569-final-rebased-v2/reports` | 0 | **`singlebox coverage artifacts verified`**；backend **49.72%**（≥38）、BaaS **45.95%**（≥45）、BCS runtime line/method/router/cli **54.18%/48.07%/100.00%/100.00%**、backend router 命中 **1257**/plugin 证据 **13**、BaaS router 命中 **297** |

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
  该 Plugin 的生产插件树实现
  `plugins/community/bcs_collaboration_metadata.py::BcnCollaborationMetadataAdapter`
  ；请求侧生产装配复用同一实现，保持 ownership_sync
  条目模型——生产 Owner/managers Http Plugin 的分母不变、旧阈值不动。

### 3.2 focused 实测与阈值

阈值确认以修复后的最新 focused `--module collaboration_repair` 运行为准：
`bash singlebox/ci/singlebox_coverage.sh --module collaboration_repair --coverage-root singlebox/.dependencies/coverage/pr3569-focused-v3`
使用全新覆盖运行目录。**exit 0：`singlebox coverage gate passed`**；acceptance
**5 passed**（含 provider-bound caller 成功修复 story）；模块实测：

| 指标 | 实测 | 设定阈值 |
| --- | --- | --- |
| Router API（`POST /openapi/v1/bots/{bot_id}/collaboration/repair`） | 100.00%（1/1） | 100.0 |
| Core Line（整个 `core/bot_management/collaboration_repair/`，225 语句） | 71.11%（160/225） | 70.0（留一行运行方差） |
| Plugin API（4 项 metadata 方法） | 100.00%（4/4） | 100.0 |

BCS E2E 同跑：coverage gate passed，endpoint **173/173=100%**，bcs-cli
**66/66=100%**；`collaboration-repair-provider.json` 凭据由 runner 的
EXIT trap 删除，未进入 reports/。caller 成功 story 的远端状态
通过 BCS 官方 Gateway provider 注册建立（provider binding 是 `/caller`
子资源的既有关系），修复本身仍只读取/改写 committed Backend facts。

保留早期的分母探索历史（不用于阈值）：无 provider 凭据的 public-local 栈曾
测得 `core=36.36% router=100.00% plugin=0.00%`（能力门 503 前置）；引入
coverage-only 临时凭据后，首次补 caller story 曾暴露两个真实边界——
`plugin` connection-mode 注册是 no-binding（`/caller` 404），provider
注册又已同事务初始化 ownership。最终 story 以 Gateway provider
binding + 已建立 caller revision 作为 fixture 前置，才断言整单
`ownership/profile/managers/caller_config` 全部 `succeeded`。

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
| **collaboration_repair（新增）** | **71.11%（≥70.0；router 100%；plugin 100%）** |
| cron | 44.91%（≥41.84） |
| expert_chat | 62.14%（≥61.18） |
| files | 69.39%（≥63.52） |
| harness | 50.75%（≥40.86） |

既有模块阈值未改动、未弱化；BCS E2E 汇总：line **54.18%**（≥41）、method
**48.07%**（≥36）、endpoint **173/173=100%**、bcs-cli **66/66=100%**。
随后对 `pr3569-final-rebased-v2/reports` 执行 `verify_singlebox_coverage_artifacts.py`：
**exit 0**，`singlebox coverage artifacts verified`（backend **49.72%** ≥38、
BaaS **45.95%** ≥45、BCS router/cli 100%/100%、backend router 命中
**1257**/plugin 证据 **13**、BaaS router 命中 **297**）。临时 Provider 凭据文件
已由 runner 清理，确认不存在后再结束。

多模块共享栈下的相互干扰检查（AGENTS.md 要求）：默认全部模块的同一栈上，本模块
story 与 owner/managers/dormant/expert_chat 等全部 story 并存运行，
collaboration_repair 复测（focused 71.11% / 全量 71.11%）一致，无共享栈干扰回退。

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
| `apps/backend/tests/community/acceptance/collaboration_repair/live_case.py` | 425 |
| `apps/backend/tests/community/acceptance/collaboration_repair/test_collaboration_repair_live.py` | 148 |
| `apps/backend/tests/community/endpoints/test_collaboration_repair_endpoint.py` | 181 |
| `docs/specs/2026-11-02-bot-avatar-collaboration-repair/backend-validation.md` | 373 |
| `apps/backend/src/agentclaw/community/core/bot_management/collaboration_repair/README.md` | 241 |
| `apps/backend/docs/bot-ownership-sync-operations.md` | 264 |
| `singlebox/ci/singlebox_coverage_modules.yaml`（新增模块段） | 427 |
| `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`（re-export 修复） | 983 |

全部 ≤1,000 行；`git diff --check` 通过；新增/修改行无密钥、无数据库迁移、
无 runtime state、无私有 endpoint、无新增依赖（pyproject 未提交变更）。

## 7. 待办追踪（非本 Task 修复）

1. （§4.3）为 `BcnService` 暴露公开 timeout 访问器并移除组合根的 `_timeout` 读取。
2. （Task 7 评审遗留）将 `CollaborationRepairPlugins` port 拆分至独立 di 模块。
3. ~~（单盒联调覆盖）共享栈 provider scope 已由 coverage-only 临时
   Provider 实现并实测上调（§3.2/§3.3，plugin 100%）。~~

## 8. 此前未执行项的最终状态

1. **Live 双侧的 200 修复报告与四项分项读回（ownership/profile/managers/caller_config）**：
   已关闭。coverage-only 临时 Provider 仅在 Singlebox coverage runner 内
   注册并被 BCS 一次性配置 allowlist；最终 focused/all-module live story 均
   以真实 HTTP 修复到 `succeeded`，并读回 owner、manager roster、profile
   和 caller applied 结果（§2.1/§3.2/§3.3）。普通 public-local 栈仍保持
   credentialless，503 能力门语义不变。
2. **caller_config 的 live CAS（stale/unchanged/applied）**：修复成功路径已
   覆盖 live `applied`。`stale/unchanged` 仍由 BCS `caller_identity_story`
   与 Backend transport/adapter 单测作为边界证据；本模块 story 不伪造双写
   分叉。
3. **剩余未执行项：Backend 创建路径的 provider 注册携带头像（create_bot →
   `_register_bot_to_bcn_as_provider(avatar_url=…)`）的 live 证据**：
   普通单盒 dev 无凭据按设计 skip（§2.1）。该路径的 payload/激活行为由
   `tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py`
   与 `test_bcn_service_register_provider.py` 钉住；BCS 侧消费由
   [bcs-validation.md](bcs-validation.md) §2 实测。
