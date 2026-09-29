# Research: TC Session Resource Withdrawal

## Status and Provenance
- 日期：2026-09-28；仅需求澄清与静态代码核对，不是实现方案批准或联调完成记录。
- 输入：用户附件《TC / Avernet 开发 Handoff：会话文件删除事件同步到知识体系》，日期 2026-09-28，原核对基线 `ce7d507d802c328e08b0d01e978b9532ba956413`。
- GitHub `origin/dev` 已 fetch；本次源码基线为 `3c3786ace7343c9a2e44578384afc82460c55445`，提交时间 `2026-09-28T15:25:44+08:00`，标题 `fix(workflow): filter resolved approval cards by bot (#2491)`。
- `git ls-remote origin refs/heads/dev` 二次核对与本地 `origin/dev`、HEAD 一致。
- 新分支：`codex/tc-session-resource-withdrawal`，直接从上述 dev 创建；未混入原分支提交，未 commit/push。
- 以下 `path:line` 均为该基线的仓库相对位置。运行时部署、真实浏览器请求和 ECB 内部行为没有验证。

## Domain and Contract References
- 文档路由：[CONTEXT-MAP.md](../../../CONTEXT-MAP.md)、[共享词汇](../../../CONTEXT.md)、[domain.md](../../agents/domain.md)。跨组件契约草案放在本目录。
- 约束：[架构宪法](../../arch/arch.rules.md)、[CI](../../arch/ci.enforce.md)、[模块边界格式](../../arch/context-boundary-format.md)、[协议一致性测试](../../arch/protocol-contract-tests.md)。
- Backend 当前边界：[session_resources](../../../src/backend/src/agentclaw/community/core/session_resources/README.md)、[tc_file_upload_integrations](../../../src/backend/src/agentclaw/community/core/tc_file_upload_integrations/README.md)。
- 已有 READY 契约：[tc_resource_ready.py](../../../src/backend/src/agentclaw/community/plugin_api/tc_resource_ready.py)、[上传通知 Spec](../../../specs/2026-09-14-tc-file-upload-completion-sidecar-protocol/spec.md)。其 best-effort 承诺不等于本需求的可靠投递承诺。
- Frontend-nextgen 当前调用契约：[Bot session files](../../../src/frontend-nextgen/src/services/backendApi/bots/botSessionFileController.ts)、[collaboration session files](../../../src/frontend-nextgen/src/services/backendApi/collaboration/sessionFileController.ts)。
- Engine 独立文件 API：[router.py](../../../src/engine/src/engine/community/api/session_files/router.py)；本期不改变其物理删除语义。
- ECB 接收契约不在此仓库内；当前只有附件草案，没有经验证的对端接口文档或就绪证据。
- 当前 ADR 目录未发现直接约束本需求的决策；Skill 的版本撤回、引用约束与本次业务文件撤销不是同一领域概念。

## Confirmed Bot UI Path
当前实现位于 `src/frontend-nextgen`，不是旧 `src/frontend`。静态调用链已闭合：

| Step | Evidence |
| --- | --- |
| 文件删除确认 | `src/frontend-nextgen/src/pages/Workspace/components/BotSessionFiles/BotSessionFilesPanel.tsx:225`，确认后 `onDelete(file)` |
| 接入文件状态 hook | `src/frontend-nextgen/src/pages/Workspace/hooks/useBotSessionFilesFeature.tsx:194`，`onDelete` 连接 `files.deleteFile` |
| 发起删除 | `src/frontend-nextgen/src/pages/Workspace/hooks/useBotSessionFiles.ts:54`，使用 `file.resourceId` |
| 服务封装 | `src/frontend-nextgen/src/services/workspace/botSessionFileService.ts:214`，调用 `deleteFile` |
| HTTP 调用 | `src/frontend-nextgen/src/services/backendApi/bots/botSessionFileController.ts:73,140`，`DELETE /openapi/v1/bots/{botId}/sessions/{sessionId}/files/{resourceId}` |
| 公共 Backend 入口 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/sessions/router.py:89,832` |
| 转调资源服务 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/sessions/dependencies_session_files.py:91` |
| 业务删除 | `src/backend/src/agentclaw/community/core/session_resources/service.py:371` |

结论：源码支持 Bot 单聊删除经过 Backend 公共服务，未发现此入口直接调用 Engine 删除 API。此结论不能替代部署版本确认或浏览器/E2E 验证。

## Separate Collaboration UI Path
群聊的文件面板不是上述组件，也不能直接将其标识当作 `res_id`：

- `src/frontend-nextgen/src/pages/Workspace/components/GroupChatPane/SessionFilesPanel.tsx:243`：确认后调用 `filesState.removeFile(file.fileId)`。
- `src/frontend-nextgen/src/pages/Workspace/hooks/useSessionFiles.ts:80` → `src/frontend-nextgen/src/services/workspace/sessionFileService.ts:210`。
- `src/frontend-nextgen/src/services/backendApi/collaboration/sessionFileController.ts:51,97`：`DELETE /api/v1/collaboration/sessions/{sessionId}/files/{fileId}`。
- `src/gateway/configs/application.yaml:458-461` 将 `/api/v1/collaboration/**` 路由到 BCS；`src/gateway/tests/unit/core/forwarding/test_domain_map.py:197` 声明文件路径原样转发，该测试本轮未执行。

范围已确认（2026-09-28）：用户明确本次仍仅实现单聊场景，不碰群聊。因此本期只覆盖 Bot 单聊 Backend 资源链路，不修改群聊 UI、协作文件 API 或 BCS 文件删除行为，不增加群聊通知，也不建立群聊 file_id 到 res_id 的映射。保留以上群聊代码证据仅用于说明排除边界，不作为实施落点。

## Backend Findings
### Current deletion
- `src/backend/src/agentclaw/community/core/session_resources/service.py:371-393`：`delete()` 只调用 repository 软删除、处理 not-found、记录日志并返回，没有删除通知。
- `src/backend/src/agentclaw/community/adapters/http/session_resources/router.py:270`：旧版删除同样调用公共服务，后续不能只覆盖正式 Router。
- `src/backend/src/agentclaw/community/core/repository/implementations/platform/session_resource.py:194-224`：`soft_delete()` 自己打开数据库 session，校验 resource/owner/bot/session，排除已 deleted 更新；重复删除可能查询并返回原记录。
- 因而“返回非空”不等于首次删除；在该方法返回后另写一条事件，并不构成删除与事件的同事务保证。

### Terminal state and existing notification
- 同 repository 的 `cas_finish_materialization()`，`:147-184`，要求任务/版本/transfer 匹配且状态为 `DEVICE_SYNCING`；这为删除终态提供现有保护，后续不能削弱。
- `src/backend/src/agentclaw/community/core/tc_file_upload_integrations/coordinator.py:58`：当前 READY 通知通过内存去重与 `asyncio.create_task()` 执行；其 README 明确没有 outbox、重试、重启恢复保证。
- `src/backend/src/agentclaw/community/plugins/community/tc_resource_ready.py:57`：READY HTTP publisher 使用状态码检查，不能直接当成新撤销协议的持久接收确认校验。
- 当前未确认单独接收 ECB processing/ready/failed 状态回传的展示接口；不应据猜测新增。
- 不保证两个独立 HTTP 通知的到达顺序；ECB 必须处理删除早于 READY/上传入库的情况。

## Handoff Constraints, Not Yet an Approved Plan
- 删除和事件登记需要真实的本地原子边界；附件建议 transactional outbox，具体契约、迁移和事务落点留待 plan。
- core 通过契约编排，repository 只负责持久化，HTTP 发布位于插件实现，DI 选择具体实现。不要让 repository 发 HTTP 或 core 依赖具体 publisher。
- 可靠投递需要稳定事件 ID、唯一性、持久状态、并发领取/租约、崩溃恢复、退避、受控重放；不能仅复制现有 READY 的内存任务。
- ECB 暂时故障不影响已提交删除；本地原子写入失败必须报错并回滚。若产品要求本地事件写失败也返回删除成功，须重新评审可靠补偿方案，不能称 best-effort 为可靠。
- 配置使用既有 Config/SecretResolver；生产缺失不能 no-op 成功。本地 fake 必须明确，不作为真实集成成功依据。
- 暂停发送应保留事件；数据库迁移及对端接收能力就绪后才启用生产投递；暂停/回滚不得清空未完成事件。

## ECB Contract Draft
以下仅转录 handoff 的协商起点，不是已发布接口，也不是 TC 当前能力：

```http
POST /api/v1/knowledge/integrations/tc/files/withdraw-by-resource
Authorization: Bearer <configured-service-credential>
Content-Type: application/json
```

```json
{"event_id":"tc.resource.withdrawn:sr_xxx","res_id":"sr_xxx"}
```

建议回执：

```json
{"event_id":"tc.resource.withdrawn:sr_xxx","accepted":true,"status":"pending"}
```

- `res_id` 与 READY 通知的原始资源 ID 一致；不传 content hash 或由 TC 提供 business_file_id。
- `event_id` 在删除/重试中稳定。草案基于资源不可复活复用的假设；若该假设不成立须增加明确的版本语义。
- `accepted=true` 只能表示 ECB 已持久化接收，允许 `applied` 或 `pending`；后者由 ECB 负责等待映射和最终应用。TC 必须校验事件匹配与约定回执，不能把任意 2xx/HTML 当成功。
- 路径、契约版本、HTTP 状态、envelope、鉴权可确定的租户/集成范围及错误分类仍待冻结；不可将 `res_id` 填入现有要求 business_file_id 的 lifecycle-events 接口。
- ECB 负责可信范围内的映射、`file_withdrawn` 状态迁移、删除先到的持久意图、乱序不复活，以及列表/摘要/检索按有效引用授权；保留 `src_id` 等追溯关系，不回收共享构建产物。

## Validation Inventory
已定位但本轮未执行的现有测试：

- `src/backend/tests/community/core/session_resources/test_repository.py:105`：删除后 materialized 回调不能完成。
- `src/backend/tests/community/core/session_resources/test_repository.py:132`：软删除幂等与 session 使用。
- `src/backend/tests/community/adapters/http/openapi_v1/engine_runtime/test_session_files.py:237`：正式文件删除复用公共服务。
- `src/backend/tests/community/endpoints/test_session_resources.py`、`src/backend/tests/community/endpoints/test_openapi_session_files.py`：相关端点覆盖。

后续验收必须覆盖：

| Area | Required evidence |
| --- | --- |
| 本地事务 | 成功原子提交、失败整体回滚、未提交事件不可投递、不存在/越权无事件 |
| 幂等与恢复 | 重复/并发删除只有一个逻辑事件；响应丢失仍复用事件 ID；重启/多实例可恢复 |
| HTTP 与契约 | 暂时错误退避；401/403、契约错误、错误 event_id/无效回执不记成功；fake 与真实 publisher 一致性 |
| 终态与入口 | 正式/旧版入口一致，未 READY 删除也登记，迟到回调不复活 |
| 真实 ECB 联调 | 删除早于上传落库仍最终失效；同内容 A/B 只撤销 A；确认最终引用状态而非仅 TC 投递状态 |
| 工程约束 | 迁移、配置、协议一致性、架构门禁与源文件行数限制 |

本轮仅建立代码证据和需求草案，没有修改业务代码，没有运行上述功能/契约/E2E 测试，没有对端部署或联调证据。
