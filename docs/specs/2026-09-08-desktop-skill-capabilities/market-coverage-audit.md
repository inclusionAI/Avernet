# Desktop 市场 / Reference / Sync 覆盖审计

结论：G1–G4 可以覆盖该链路，但必须显式补上 **Reference 最终 `add_skills` → Desktop Skill 恢复** 的交接与测试。已有市场查询、Public 云端物化、Reference 持久任务和正式 SkillSet 写入均应复用；不能把 Reference `COMPLETED`、版本 `PUBLISHED` 或 Track Latest 已入队当成设备已生效。

## 基线和证据边界

- A：Avernet `ca308268927f10df887ad3543d664244dbe6ce52`，仓库 `<Avernet research checkout>`。下文 A 源码均用 `git show/git grep` 固定该 SHA，而非当前 checkout。
- O：OCB `658abdf1cc88b047c73463463c695510866b4759`，仓库 `<OCB research checkout>`。`git ls-tree O ocb-public` 为 `137219270c46a3508daeaf92d9e660e5ee38aeb9`；与 A 是两种集成快照，不能当部署证据。
- 研究依据包括两仓架构与模块约束。本审计只读固定代码；未执行测试或线上API，不代表企业部署已验证。
- 真实 TeamClaw 前端未现场核验。**保留用户已确认“SC 详情以 iframe 实现”的事实**；仓库内 OCB 旧前端及 A `frontend-nextgen` 只能作为调用方样例，不是当前线上前端权威，不能据此判定产品详情未实现或追加市场 UI 重写。

## 现有链路与工程包映射

| 链路 | 当前事实 | Desktop 覆盖责任 |
| --- | --- | --- |
| TeamClaw Repo 市场查询、详情、添加 | POST `/openapi/v1/bots/market/skills` 只读 `git://` 资产；GET `/openapi/v1/bots/skills/repository`、`/repository/{skill_id}` 提供目录与详情；已有资产通过 PUT `/{bot_id}/skill-sets/{set_id}/skills/{skill_id}`，最终进正式 `add_skills`。Legacy 批量路由先解析历史名称/path 为 ID，再一次正式 add。[A1–A3] | 复用查询/权限与正式写入，不为 Desktop 重做 Repo 市场；G1 相关回归，G3 原有 Repo/Local 映射兼容，G4 正式变更恢复入口覆盖。 |
| SC Public 搜索、详情 | POST `/openapi/v1/bots/market/skill-center/skills`、GET `/market/skill-center/tags` 已有。Backend `get_public_skill` 是 Gateway 方法；O Corp 实现读取 SC 精确 code 详情，SC 凭证留在服务器。现有 market OpenAPI router **没有新 Public-detail HTTP route**；不需要为了 Desktop 发明一个，前端 iframe 详情保持现状。[A4,O1] | 共享资产链路仅回归；G1 检验既有内容入口，不能把查看市场列表变成下载全部内容。 |
| Public Reference 创建、列表、详情 | POST `/{bot_id}/skill-sets/{set_id}/skill-center-references` 要求 `Idempotency-Key`、`skill_codes`，返回 202、`request_id/reference_ids`；GET collection 可按 request/status 分页，GET `/{reference_id}` 查逐项。owner+bot+set+tenant/env 冻结/过滤。[A5,A6] | 既有 API 与鉴权复用，G1/G4 按真实合同联调；202 只表示接收。 |
| 重复请求与“重试” | 去重保序，去重后 1–20 code；同 key+同命令重放原 batch 并 ensure task，同 key 不同请求报冲突。Gateway/物化暂态失败内部至多 3 次；**没有独立 Reference retry HTTP route**。终态 item 不再处理，因此重发同 key 不是重置 FAILED；换新 key 是新 Reference 命令。[A5–A7] | 必测重复接收、ensure-task 窗口、部分成功/失败；不新增 retry 路由、进度表，也不把下载等待塞回 Reference 任务。 |
| 云端懒物化、Ready 共享复用 | 每项查 Public detail/latest exact version，按 tenant/env/code 派生 Public 身份，复用同 locator/同 exact Version；不按同名复用 Local/Repo/Space。Materializer 已有 `PUBLISHED` 分支先 verify Canonical 后直接复用；新版本核对 exact code/version、下载 SHA、包结构、Canonical 写入/验证，最后置 `PUBLISHED`。MCP 元数据来自 exact response。[A7–A9] | 不复制 SC 状态机/鉴权；G3 在已物化 Canonical 上补精确派生 ZIP/限时签名描述及 Desktop 下载缓存，而不是再次让 Desktop 访问 SC。 |
| Ready → 正式 SkillSet/Installation | 所有可成功项经过 barrier 后一次 `add_skills`，重新验证 Bot/Set/权限/Offline；部分 item 失败不阻断成功项。active Set 写 Membership 与 Installation 后一次最终投影；inactive Set 仅 Membership，返回 `SKIPPED/RUNTIME_NOT_REQUIRED`，不因引用成功要求 Desktop 下载。已有 Membership 返回 changed=false，可跳过投影。[A7,A10,A11] | G4 从正式共同变更入口接恢复，覆盖 Reference 而不是旁写 Installation。active/inactive、同 Set 重复、跨 Set/Direct 冲突、物化后权限/Offline 变化均必测。 |
| 后续周期/手动 Sync | POST `/openapi/v1/bots/market/skill-center/sync` 与启动/周期执行同一 `SkillCenterSyncService.sync`。仅枚举有 PUBLISHED Version 的 Public Center 且无 Space binding；不导入全市场。代码默认周期 30 分钟，固定锁 TTL 30 分钟；现场生效值未核验。每次 exact materialize 后即使已 PUBLISHED 也重新 ensure Track Latest。[A4,A8,A12] | 复用服务与协调；G3 验证 exact 更新内容，G4 验证 Sync→Track Latest→Desktop 新版本切换；不把资产 Sync 计数当设备生效结果。 |
| Track Latest → Desktop | fanout 按当前候选事实排逐 Bot/Skill 任务；Bot handler 重新 Reader，已不 active 则完成；PENDING 返回 Retry，DEGRADED 记录后继续，现有 scope 含 Skill+MCP，任务 deadline 30 分钟。[A13] | G4 明确接 Desktop Skill-only 后续恢复，保留现有首次 MCP best-effort；新恢复重试不得每轮重跑 MCP/Passport，也不得用旧 exact ref/URL 代替最新 Reader。 |

## 必须补齐的交接：不是新市场系统

1. **`COMPLETED` 不等于 Runtime Ready，且当前没有可持续查询的 Runtime 结果。** `SkillSetSkillOutcome.succeeded` 仅检查 error；正式 service 把 `runtime_projection` 附到成功 outcome，但 Reference processor 只读 succeeded 然后置 `COMPLETED`，没有读取该 projection。Reference DB model、domain DTO、HTTP DTO 均无 `runtime_projection` 字段。[A5,A7,A10,A14] 保留 `COMPLETED=已加入能力集`；文档/实机验收不得报成“设备已生效”。新增 Runtime 轮询 API/表是另一公开合同，本期未批准。
2. **物化完成的 Track Latest 可能在最终 add 前抢跑。** processor 在 materialize 后立即 `version_published`，随后才置 ADDING 并最终 add；fanout 当时可能没有该 Bot 的 Membership/Installation，因此产生零候选后完成。[A7,A13] 它修的是物化后的 enqueue 窗口，不是首次 Reference 的“加 Set 后确保恢复”。G4 必须从正式 add/共同变更结果交接 Skill 恢复，不依赖再次发布、周期 Sync 或用户第二次点击。
3. **恢复接线不能只放在 Engine 返回下载 PENDING 后。** `_mutation_flow.apply` 的 Bot 未 Ready 提前分支先写 DB 后返回 PENDING；snapshot/resolve 异常也可不进入 apply 就返回 PENDING。[A11] G4 应覆盖这些入口及队列 ensure 失败/进程中断窗口，并用已批准的扫漏策略收口；inactive SKIPPED 不是要补下载的 PENDING。
4. **共享版本复用与重复 Membership 也必须可收口。** 物化 PUBLISHED 复用、同 Set 重复 changed=false 均合法；后者可能 `DESIRED_STATE_UNCHANGED` 跳过本次投影。[A8–A11] 验收要覆盖旧 Reference 已 COMPLETED、设备仍缺缓存/新绑定的恢复，不把重新创建共享资产、重发历史 add 或重新发布当补救条件。
5. **状态映射要逐层证明。** 最少区分 Reference item FAILED、Reference COMPLETED+inactive（无 Runtime 要求）、Reference COMPLETED+active+Desktop PENDING、最终 Runtime 对当前 exact version 生效。保留部分成功和共享资产，不把权限/DB 失败伪装成 PENDING；最终 Ready 必须有 Engine 缓存/目标软链/可读 `SKILL.md` 的真实证据。[A7,A9–A11,A14]

G2 对市场元数据/Reference JSON 没有新的物化职责；其 bytes 保真主要保护本期 Local 文件通道。G1/G3/G4 自带相应窄测试与跨仓 DI/合同门禁，不能把所有正确性测试推迟至 G4。G3 单轮下载+映射通过仍不等于整个市场添加 Desktop 链路可上线。

## 最小验收增量（接入 G1/G3/G4，非已执行结果）

- 保留现有测试的 dedupe/20 项/同 key 冲突、单次 batch add、缺失项部分成功、Offline 竞争保留共享资产、已 PUBLISHED ensure fanout；已有对应测试入口见 [T1–T3]，本次未运行。
- 新增交接用例：先执行 fanout 得零候选，再完成最终 active add 且 Runtime PENDING，证明独立 Skill 恢复任务仍被 ensure；覆盖 Bot 未 Ready、snapshot 不可达及 Engine 正常下载 PENDING 三种分支。
- 同一批成功/失败混合：成功 Membership/Installation 保留、失败项错误可查；Reference 的终态不作为 Runtime 收敛条件，后台补齐无需用户第二次操作。
- inactive Reference 完成后零下载；后续 activate 才按最新 Reader 下载。移除/停用/改绑定后，迟到下载不得复活旧期望态。
- 两个 Bot 引用相同 Public code/exact version 复用云端资产与完整 canonical；Desktop 缓存只复用经过当前规则验证的 exact 内容。后续手动/周期 Sync 发布 V2，Track Latest 单轮 MCP 语义不变，后续 Skill-only retry 零 MCP/Passport 调用。
- OCB 真实装配、BaaS、客户端/Engine 版本及 OpenClaw/Hermes 实机分别给证据；线上前端确认仍使用正式 Reference 路由（不能走 Legacy 占位 install），展示“已添加”与“设备生效”的边界一致。用户已确认的 iframe 详情不改造。[A15,O1,O2]

## 固定源码索引

下列 `A/O:path:行号` 都指上方固定 SHA；可用 `git show <SHA>:<path> | nl -ba` 复核。

- A1 `src/backend/src/agentclaw/community/adapters/http/openapi_v1/market/router.py:120-140`；`src/backend/src/agentclaw/community/core/skill_center/services/skill_market_service.py:17-78`。
- A2 `src/backend/src/agentclaw/community/adapters/http/openapi_v1/repository_catalog.py:32-100`。
- A3 `src/backend/src/agentclaw/community/adapters/http/openapi_v1/skill_sets/router.py:250-286`；`src/backend/src/agentclaw/community/adapters/http/skill_center/skillsets.py:754-869`；`src/backend/src/agentclaw/community/core/skill_center/services/skill_set_management_service.py:306-350`。
- A4 `src/backend/src/agentclaw/community/adapters/http/openapi_v1/market/router.py:192-285`。
- A5 `src/backend/src/agentclaw/community/adapters/http/openapi_v1/skill_sets/skill_center_references.py:25-106,131-231`。
- A6 `src/backend/src/agentclaw/community/core/skill_center/services/skill_center_reference_service.py:57-123,169-217`；`src/backend/src/agentclaw/community/core/repository/implementations/skill_center/skill_center_reference.py:381-445`。
- A7 `src/backend/src/agentclaw/community/core/skill_center/services/skill_center_reference_processor.py:100-185,199-280,305-357,360-478`。
- A8 `src/backend/src/agentclaw/community/core/repository/implementations/skill_center/skill_center_reference.py:233-379,447-484`。
- A9 `src/backend/src/agentclaw/community/core/skill_center/services/skill_version_materializer.py:159-277`。
- A10 `src/backend/src/agentclaw/community/core/skill_center/services/skill_set_management_service.py:306-350,447-539`；`src/backend/src/agentclaw/community/core/repository/implementations/skill_center/capability_desired_state.py:301-439`。
- A11 `src/backend/src/agentclaw/community/core/skill_center/services/_mutation_flow.py:137-157,161-220,233-282`。
- A12 `src/backend/src/agentclaw/community/core/skill_center/services/skill_center_sync_service.py:58-110,111-154,156-193,195-251`；`src/backend/src/agentclaw/community/di/modules/skill_center_group4_module.py:174-199`。
- A13 `src/backend/src/agentclaw/community/core/skill_center/services/track_latest.py:39-59,78-106,145-200`；`src/backend/src/agentclaw/community/core/skill_center/track_latest_contract.py:25-72`。
- A14 `src/backend/src/agentclaw/community/core/skill_center/skill_set_batch.py:9-25`；`src/backend/src/agentclaw/community/core/skill_center/reference_contract.py:45-57`；`src/backend/src/agentclaw/community/core/models/skill_center_reference.py:106-125`。
- A15 `src/backend/src/agentclaw/community/adapters/http/skill_center/skills.py:2932-2981`（Legacy Public 搜索/标签与仍然占位的 install；不是正式 Reference 替代）。
- O1 `src/backend/src/agentclaw/corp/plugins/prod/skill_center_gateway.py:111-168,348-430`；`src/backend/src/agentclaw/corp/di/modules/infrastructure/corp/skill_center.py:53-68`（公开 Gateway 由 Corp 凭证/HTTP 实现装配）。
- O2 `src/frontend/src/services/backend-api/SkillController.ts:135-152,516-546`；`src/frontend/src/services/backend-api/SkillsetController.ts:115-139`（旧 frontend consumer 样例，非线上权威）。
- T1 A `src/backend/tests/community/core/skill_center/test_skill_center_reference_processor.py:202-352`。
- T2 A `src/backend/tests/community/core/skill_center/test_skill_center_reference_service.py:115-159`；`src/backend/tests/community/repository/skill_center/test_skill_center_reference_repository.py:60,185,284`。
- T3 A `src/backend/tests/community/core/skill_center/test_track_latest.py:180-321`；`src/backend/tests/community/integration/skill_center/test_reference_offline_cross_seam.py:189`。

仓库内非权威 consumer 例子：A `src/frontend-nextgen/src/services/backendApi/bots/botEditorController.ts:205-253` 使用 Repo catalog、SC search、Reference POST/collection；`src/frontend-nextgen/src/services/botWorkshop/botEditorService.ts:255-271` 用有限轮询判断 Reference 终态。它只能说明 API 的一种消费方式，不能证明当前 TeamClaw 页面行为或 Desktop 上线状态；本审计不据此追加 UI 修复范围。
