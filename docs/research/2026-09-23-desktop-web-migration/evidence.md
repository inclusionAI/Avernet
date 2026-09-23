# 源码证据索引

配套 [评审草案](./README.md)。B/N/L 的完整 SHA 与可信范围见主文档。行号对应记录的本地 HEAD；后续变更需重新核对。本轮仅静态阅读、检索和 Python AST 路由提取，没有构造 mock 或调用真实业务写接口。

## B：当前后端

以下相对路径以 Avernet 仓库根目录为基准。

| 编号 | 源码 | 直接证据与结论边界 |
| --- | --- | --- |
| B-E01 | `src/backend/src/agentclaw/community/adapters/http/openapi_v1/local/router.py`：123/169/211/262/305/334/399/421/441 行函数 | 设备、目录、创建、列表、详情、授权、重启、删除、打开目录 9 条正式路由。没有 start-progress |
| B-E02 | 同文件 `create_local_bot`、`local_bot_auth_status`、`_require_user_delegation` | 当前创建使用 DelegatedUserDep、grant_the_creating_app；auth-status 为 GET 且有 own-bot grant；设备查询接受 holds_delegation。与旧分析的 human-only 结论不同 |
| B-E03 | `.../openapi_v1/__init__.py` local_router 注册；`.../di/modules/bot_inventory_module.py` desktop_inventory_port/local_bot_workflow_service | local Router 已注册，工作流经 Protocol 复用 DesktopBotService；并非只有孤立 Router 文件 |
| B-E04 | `.../openapi_v1/local/schemas.py` LocalBot/LocalBotCreate/LocalDevice | 创建 engine/machine_id/mount_path；返回 LocalBot 有 machine/path/avatar，无 bot_type；目录路由返回 dict 而非 LocalDirectoryEntry |
| B-E05 | `.../core/bot_inventory/services/local_bot_workflow.py` require_personal_space/start_create/poll_auth_status/list_device_files | 个人空间限制；复用 Passport/桌面创建/授权关系；目录向下只传 machine_id/directory。后者仅证明 owner 没有在此继续传递，**不足以直接断言全链路存在越权** |
| B-E06 | `.../core/bot_inventory/policies/combo_policy.py` LOCAL_CAPABLE_ENGINES | openclaw/claude_code/hermes；不能推导所有已部署客户端都支持 |
| B-E07 | `.../core/bot_inventory/policies/action_policy.py` actions_for | local_running 允许 view/chat/edit/restart/delete/open_folder，但 runtime_logs/engine_restart disabled；这是动作声明，不能证明 chat 已可用 |
| B-E08 | `.../core/engine_runtime/gate.py:76`、`:165`；`.../core/engine_runtime/connection.py:213` | SUPPORTED_BOT_TYPES 仅 personal/service；不匹配抛 EngineBotTypeNotSupportedError；普通连接 build 也调用此 gate |
| B-E09 | `.../openapi_v1/engine_runtime/gating.py`；sessions/engine/models/approvals routers | resolve_operable_bot 共享 gate；文档明确映射 501。具体 friend-chat 分支不能从普通 operator 路径推断 |
| B-E10 | `.../openapi_v1/engine_runtime/engine/router.py` restart_bot_engine | 通用 engine/restart 是 daemon restart；与重新创建容器或旧桌面 restart 语义不同 |
| B-E11 | `.../adapters/http/desktop/router.py:394` get_bot_start_progress | legacy 以 bot_uuid 和 device_affinity 查询 BaaS；新增 OpenAPI 应自行做 Bot 身份/owner 解析，不能直接暴露任意 uuid |
| B-E12 | `.../openapi_v1/bots/schemas.py:282` BotUpdate；`.../openapi_v1/bots/router.py` update_bot | 更新只接受 name/desc，extra forbid；没有 avatar 写入参数。不是“前端传 avatar 即可” |
| B-E13 | `.../adapters/http/resources/router.py` batch_create_link_resources/update_link_resource；`.../openapi_v1/resources/router.py` 模块注释 | legacy 关联链接和授权同步实现存在；OpenAPI resources 明确 links 不属于此组；deprecated/resources 仅重定位文件接口，不补 links |
| B-E14 | `.../openapi_v1/identity/router.py`；`.../openapi_v1/bots/engine_config.py` | identity list/read/write、engine/config GET/PUT 存在；注入 IdentityService/EngineConfigService，不是 runtime gate 同一路由 |
| B-E15 | `.../core/services/identity.py`、`.../core/services/engine_config.py` resolve_stage_device_context；`.../core/devices/services/device_context_resolver.py` desktop 分支 | 文件/配置有设备寻址基础；真实桌面路径/字节/保存生效仍需联调，不能据此标全部可用 |
| B-E16 | `.../core/bot_config_manifest/capabilities.py` desktop 判断 | desktop 明确不在 manifest 范围；迁移旧配置页无须据此扩容 manifest |
| B-E17 | `.../openapi_v1/skills/router.py`、skill_sets/router.py、mcp/、market/、routines/router.py、render_screens/router.py | 通用能力端点存在；参数 GET/PUT、上传新增/替换、render-screens CRUD 可定位；实际 desktop 交付证据与 API 存在分开 |
| B-E18 | `.../core/desktop_bot/lifecycle.py` startup/_periodic_health_loop；`.../core/desktop_bot/services/desktop_bot_service.py` create_after_authorization | 后台扫描和既有创建幂等实现；不是“全部待开发”。后台实际配置/客户端运行未验证 |
| B-E19 | `.../core/skill_center/AGENTS.md`；`.../core/skill_center/services/desktop_skill_recovery.py`；`docs/adr/0015-event-driven-desktop-skill-recovery.md` | 必须区分安装关系、内容分发和 runtime；已有恢复代码，不能把历史计划的未开始当当前实现状态 |

`...` 在 B 表中统一为 `src/backend/src/agentclaw/community`。

## N：指定新版前端

相对路径以 `/Users/liaoxianhao_1/IdeaProjects/teamClawPre/teamclaw` 为根。

| 编号 | 源码 | 证据 |
| --- | --- | --- |
| N-E01 | `src/services/botWorkshop/botWorkshopService.ts:178` | create(local) 明确 throw 未接入；remove/restart 已走专用 local API |
| N-E02 | `src/services/backendApi/bots/botController.ts` BOT_ENDPOINTS/restartLocalBot/deleteLocalBot | local restart/delete Controller 存在；未有完整 devices/create-local/auth-local/open-folder 封装 |
| N-E03 | `src/components/BotWorkshop/CreateBotModal/CreateBotFormFields.tsx`；`src/hooks/useBotWorkshopCreateFlow.ts`；`src/hooks/useBotCreateAuthorization.ts` | 通用引擎表单与云端创建授权编排；本地设备/挂载参数未接入 |
| N-E04 | `src/services/botWorkshop/botMapper.ts:139` / `:147` | bot_type 缺省 personal；仅 desktop 推导 local；local_offline 映射通用 offline；machine/path 未进入现有 BotDomain 映射 |
| N-E05 | `src/domain/botWorkshop.ts` BotInventoryAction/BotManagementVerb | 没有 open_folder；前端执行分发仍需补，不是仅后端 actions 增加即可 |
| N-E06 | `src/services/backendApi/bots/privateBotSessionController.ts`；`src/services/workspace/botChatProvider.ts` | 使用 bot-first sessions/models/connection；初始化和发送前取 IAM token；需要和旧桌面连接/身份行为对照 |
| N-E07 | `src/services/backendApi/bots/botEditorController.ts` | skills/sets/MCP/resources/identity/engine/config/render-screens 已有 Controller；没有等价关联链接管理。Controller 存在不等于所有桌面页面已完成 |
| N-E08 | `src/services/botWorkshop/botEditorService.ts`、`botAdvancedConfigService.ts`；`src/services/backendApi/bots/botRoutineController.ts` | 通用编辑/配置/定时任务已有；需按旧桌面矩阵收口和验证，不重建整个模块 |
| N-E09 | `src/services/backendApi/bots/botSessionFileController.ts` | 通用会话文件上传存在；旧 desktop 关闭该能力，不能因通用组件复用意外开放 |
| N-E10 | `src/services/backendApi/collaboration/`、`src/services/workspace/` | 新版已有协作组件和接口层；仅为复用基础，不作为 desktop 模式、权限与 BCS 实测证据 |
| N-E11 | `AGENTS.md`、`config/config.ts`、`package.json` | Component→Hook→Service→Store/API；Bigfish 与 internal overlay；本轮只读，不运行 mock 或改配置 |

## L：用户确认的旧 Web

相对路径以 `/Users/liaoxianhao_1/IdeaProjects/legancyTeamClawFrontend/open-claw` 为根。

| 编号 | 源码 | 直接观察 |
| --- | --- | --- |
| L-E01 | `src/pages/Bootstrap/BotManager/CreateBotModal.tsx:474`、`:505`、`:890`、`:1442` | 设备发现和自动选择；absolute_path + 名称自动挂载；创建前等待非空路径；手选/显示挂载区域不是当前开放交互 |
| L-E02 | 同文件授权轮询；`src/hooks/useBot.ts`；`src/internal/bootstrap/createBotAuthorizationFlow*` | 两阶段授权；最终创建参数需贯穿整个流程 |
| L-E03 | `src/hooks/useDesktopBotStartProgress.ts`；`src/utils/desktopStartProgress.ts`；`src/pages/Bootstrap/BotManager/DesktopStartProgress.tsx` | 真实进度四步、3 秒轮询、失败重试、取消轮询、步骤不倒退；不以固定时间模拟完成 |
| L-E04 | `src/pages/Bootstrap/BotManager/BotRow.tsx:163`、`:233`、`:250` | PENDING/FAILED 且有 device_id 才显示启动进度；桌面重启引擎与重启 Bot 同一接口 |
| L-E05 | `src/utils/platform.ts:86`；BotRow 离线 Tooltip；CreateBotModal 无设备入口 | teamclaw://open 唤起，非 Electron 应用迁移需求 |
| L-E06 | `src/internal/bootstrap/accessControl.ts`；`src/pages/Bootstrap/DesktopBotGuide/index.tsx`；`src/utils/botCount.ts` | 旧桌面引导及不占云端 quota；运行期准入配置需另确认 |
| L-E07 | `src/adapters/engine/BaseEngineAdapter.ts:463`；`OpenClawAdapter.ts:160`；`HermesAdapter.ts` | desktop 默认关闭若干管理/上传；OC 特例保留 Markdown/engine config；Hermes 与 OC 能力不相同 |
| L-E08 | `src/adapters/engine/__tests__/openClawDesktopFeatures.test.ts` | 测试文字明确 OC 桌面 MD/配置开放、会话文件关闭；本轮未执行，不声称测试通过 |
| L-E09 | `src/pages/Assistant/Chat/BotSettingsDrawer/tabs/BotBasicInfoTab.tsx:208`；`src/internal/components/BotAvatarUploader/index.tsx` | 旧更新包含 avatar_url；头像有生成/上传入口，上传依赖内部 SDK，不能假设 OpenAPI 已覆盖 |
| L-E10 | `src/pages/Bootstrap/BotManager/Engine/EngineConfigDrawer/index.tsx`；BotRow:939 | 引擎配置抽屉有保存逻辑，不能只迁一个只读 JSON 展示 |
| L-E11 | `src/pages/Assistant/Resource/ResourceTab.tsx:65`、`:378`；`src/hooks/useResources.ts:146` | desktop 资源文件只读、打开根/子目录、目录下载限制、1MB 预览限制 |
| L-E12 | `src/pages/Assistant/Resource/LinkTab.tsx:44`、`:241`；AddLinkModal；`src/services/backend-api/ResourceController.ts` | desktop 例外允许关联链接管理；不能将文件只读扩大成所有资源只读 |
| L-E13 | `src/pages/Assistant/Session/ConversationTab.tsx` | 会话搜索、sessionKey 定位、收藏、新建/改名/删/清空等入口 |
| L-E14 | `src/pages/Assistant/Chat/ChatPage.tsx:540`；`src/hooks/useModels.ts:109` | 桌面与云端连接/模型/IAM条件有差别，不能仅复用新云端 provider 并声明无差异 |
| L-E15 | `src/pages/Assistant/Skill/SkillTab.tsx`、SkillModal、SkillDetailDrawer；`src/internal/shell/slots.ts:161` | Skill/MCP、技能集和上传/替换/删除等能力页由 internal slot 注入；具体参数和市场权限仍需按正式入口验收 |
| L-E16 | `src/pages/Assistant/Chat/BotSettingsDrawer/index.tsx`；tabs/BotMarkdownTab、BotPanelConfigTab | MD、副屏的入口按 supports 判断；不直接按是否 desktop 一刀切 |
| L-E17 | `src/pages/GroupChat/index.tsx`；components/BotInfoCard.tsx；OpenClawAdapter/HermesAdapter canJoinBcn | OC 桌面 BCN/好友确认限制；引擎能力各异 |
| L-E18 | `src/utils/botRestartOperations.ts`；`src/pages/Admin/components/BotInventory/` | 管理台含 desktop 展示，isAdminRestartableBot 排除 desktop；不能把管理台批量重启列成已支持功能 |
| L-E19 | `src/internal/components/memory-wiki/model.ts`；memoryOS/AssistantMemoryOSSlots.tsx | 模板/capabilities 动态开放。没有历史 desktop 实例/配置证据，不自行定为桌面既有功能 |

## 未验证的边界

- 旧 Web 基线及 OpenClaw/Hermes 范围已获用户确认；生产部署 commit、这两种引擎存量实例的动态配置仍未验证。
- 网关/企业 DI 的实际装配、桌面客户端版本、BaaS/Engine 的运行时结果。
- 关联链接和头像的外部 SDK/服务是否获准作为 OpenAPI 之外的调用。
- 没有浏览器网络抓取、真实请求、新旧交叉操作和真实设备端内容/hash/会话结果。
- 本次读取的是已有测试代码；没有把测试名称、mock 返回或旧设计计划计为已验证支持。

## 本次确认后的连接补充证据

| 编号 | 源码 | 事实与边界 |
| --- | --- | --- |
| B-E20 | `src/backend/src/agentclaw/community/core/engine_runtime/connection.py:334` | `_get_connection` 固定 `ws_conn_mode="relay"`；当前普通 OpenAPI 连接不是桌面 localhost 策略 |
| B-E21 | `src/baas/src/secbaas/community/core/service/paas/_local_paas_service.py:665` | `resolve_ws_conn_info` 已有 direct/relay 分支；direct 查询真实映射端口并返回 localhost WS，token 为空；relay 有建会话和 open_ws_relay 调用实现。源码存在不代表部署可用 |
| B-E22 | `src/backend/src/agentclaw/community/core/devices/services/local_device_service.py:430`、`:760` | 现有通用连接组装会合并 WS 与 HTTP info；`path` 在该 provider 被忽略，返回 target/HTTP URL。不能直接将其 HTTP token/url 当 WS 信息，需单独保证 WS 模式、端口、引擎路径、凭证匹配 |
| B-E23 | `src/backend/src/agentclaw/community/core/devices/services/device_service.py:1741` | desktop/local 的 get_device_connection_v2 走 BaaS invoke-http 分支；管理 HTTP 与浏览器 localhost WS 可分别路由 |
| L-E20 | `src/stores/connectionStore.ts:201`；`src/pages/Assistant/Chat/ChatPage.tsx:540` | local/desktop 根据真实 target 直连 WS；本地不设置云端 Caller 换签参数。仍有连接 token 刷新逻辑，不能理解为所有鉴权都可删 |
| N-E12 | `src/services/workspace/botChatProvider.ts` getChatUrl/initialize/request | 实际代码直接返回 socket.url 并传 SDK；“提取 path 后重建 host”只出现在注释，不能作为当前行为证据。明确缺口是初始化/刷新/发送统一 IAM 策略和缺少 desktop 模式合同 |
| N-E13 | `src/services/workspace/supportProvider.ts:54` | 另一条 support/private 链路已有 local/desktop WS target 组装；只是复用参考，不能证明 BotChatProvider 已接入 |
