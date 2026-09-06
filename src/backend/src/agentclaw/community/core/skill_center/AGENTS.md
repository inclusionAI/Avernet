# Skill Center：模块架构与维护约束

核验基线：2026-09-07，`github/dev@62307f0aa`。本文件是 Skill 模块的统一维护说明，覆盖 OpenAPI、Legacy BFF、后台任务及 Runtime 消费边界。修改这些链路时先读本文件，再按下表打开对应实现。行为变更须同步修改本文件及相应合同测试。

公开 HTTP 合同以 Router、DTO、Authorization/Admission 声明为准；领域合同以 Protocol、状态枚举和测试为准。本文件记录已实现语义及职责约束。发现冲突时以代码和测试定位差异并显式修订，不能把旧 Spec 的规划当成已经实现的功能。

下文 `community/` 指 `src/backend/src/agentclaw/community/`；简称 `services/` 指本目录的 `services/`。仓库根 `AGENTS.md` 和 `docs/arch/` 仍规定通用架构约束。本目录 `README.md` 的 Context Boundary 继续承担机器可检验的依赖声明。

## 1. 从 OpenAPI 进入

`community/adapters/http/openapi_v1/__init__.py` 是 Router 装配入口。所有下列路径均位于 `/openapi/v1/bots`；Space-owned 资源只是使用这个命名空间，不因此归属于某个 Bot。

| 产品链路 | Router（位于 `community/adapters/http/openapi_v1/`） | 主要服务及后续入口 |
| --- | --- | --- |
| Bot Skill 列表、详情、内容、参数 | `skills/router.py`：`/{bot_id}/skills`，`/{skill_id}`、`/content`、`/parameters` | `SkillQueryService`；有效态经 `BotCapabilityStateReader`，内容按来源解析 |
| Bot-local 上传、删除 | 同文件：`/upload`、`/upload-folder`、DELETE `/{skill_id}` | `LocalSkillUploadService`、`LocalSkillDeleteService`；设备文件操作和资产维护 |
| 单 Skill 激活/停用 | 同文件：`/{skill_id}/activate`、`/deactivate` | `DirectActivationService` → Desired-State UoW → Runtime Projector |
| 共享资产 README、兼容发布状态 | 同文件独立 Router：`/skills/{skill_id}/readme`、发布状态资源 | README 不要求目标 Bot；不要和 Bot 参数值、Space Publication Attempt 混用 |
| SkillSet 管理与 Skill/MCP 成员 | `skill_sets/router.py`：`/{bot_id}/skill-sets` | `SkillSetManagementService` → Desired-State UoW → Runtime Projector |
| SC Public 懒引用 | `skill_sets/skill_center_references.py`：`/{bot_id}/skill-sets/{set_id}/skill-center-references` | `SkillCenterReferenceService` → 持久任务 → `SkillCenterReferenceProcessor` |
| TeamClaw 市场、SC Public 市场、手动巡检 | `market/router.py`：POST `/market/skills`、`/market/skill-center/skills`、`/market/skill-center/sync` | `SkillMarketService`、`SkillCenterGatewayService`、`SkillCenterSyncService` |
| Space Skill 创建、列表、详情、Version、下线、复制 | `spaces/skill_routes.py` | `SpaceSkillApplicationService`、`SpaceSkillQueryService`、`SpaceSkillVersionQueryService`、`SpaceSkillOfflineService` |
| Draft 文件、升级、Git 刷新、删除 | `spaces/router.py`：`/spaces/{space_id}/skills/{skill_id}/draft/...` | `SpaceSkillApplicationService` → Draft Repository + `DraftContentStore` |
| Owner/Manager、编辑租约、编辑权限申请 | `spaces/router.py`：Skill grants/managers/owner、`draft/lease`、`editor-requests` | `SpaceSkillGrantService`、`DraftEditLeaseService`、`SpaceSkillEditorRequestService` |
| 发布影响面、发布、查询 Attempt、重试 | `spaces/publication_routes.py` | `SpaceSkillPublicationService` → Publication Repository → TaskQueue → Publication Worker |

精确 HTTP method、分页、状态码和请求字段直接检查以上 Router 与同目录 `schemas.py`。例如已有资产加入 Set 使用 PUT `/{set_id}/skills/{skill_id}`；SC Public 引用使用专门的异步 POST，不能混用两种身份。

Router 使用 `PublicAPIRoute`、`ActingCaller`/scope dependencies、`Injected(Protocol)` 与 `envelope_errors`。修改接口需同时检查 `authorization.py`、`admission.py`、`errors_skill_center.py`、`errors_space_skill.py`，以及上层 Router 的注册和功能开关。`AdmissionMode.REFUSED` 的含义需按调用者模式理解，不能解释成产品接口整体不可用。

`src/gateway/configs/schemas/bots.openapi.json` 是生成产物。改公开 Router/DTO 后使用 `src/gateway/scripts/dump_and_publish.sh` 及其门禁生成，保持 Backend Router 为合同来源。

## 2. 两条主链路及事实所有权

```text
Bot 使用能力：Router → Query / DirectActivation / SkillSetManagement
                            ↓
              CapabilityDesiredStateRepository（DB 事务）
                            ↓
              Installation → Reader → VersionResolver
                            ↓
              RuntimeProjectionResolver → BotRuntimeProjector → Engine

Space 管理资产：Router → Draft / Grant / Lease / Publication / Offline
                            ↓
                  Draft OSS + 领域 Repository
                            ↓ Publication Task
                  SC 精确版本 → Materializer → Canonical Store
                            ↓
                  PUBLISHED Version → Track Latest Task → Bot Projector
```

| 事实 | 持久化及边界 |
| --- | --- |
| Skill 资产身份、来源、当前 Draft、Offline | `community/core/models/skill.py` 的 `ac_skill` |
| Space 归属、Owner/Manager | `community/core/models/space_skill.py` 的 `ac_skill_space_binding`、`ac_skill_grant`；旧 `ac_skill_member` 不是新 Space Grant |
| Draft 租约 | 同文件 `ac_skill_draft_edit_lease`；holder 与递增 fencing token |
| 不可变已发布版本、发布过程 | 同文件 `ac_skill_version`、`ac_skill_publication_attempt`；Version 与 Attempt 是不同对象 |
| 异步市场引用 | `community/core/models/skill_center_reference.py` 的 batch/item 两张表 |
| Bot 生效身份 | `ac_bot_skill_installation`、`ac_bot_mcp_installation`；行存在表示 active，不能当作“安装过但停用”的历史记录 |
| Set、成员和 Default exclusion | 组织/规则事实，由 UoW 物化到 Installation；不是第二套有效态读取算法 |
| Runtime 文件、软链、MCP 配置 | 设备投影结果，不是 DB Desired State；存储内容存在也不等于 Skill 已激活 |

Bot 定位必须携带 `owner_id + bot_id`，并保持 Repository 的 tenant/env 过滤。`bot_id=default` 在不同 Owner 下可重复；actor/user_id 是调用者，不能替代 Owner。旧表仍使用 `bolt_id` 字段时按现有映射处理。

## 3. Bot 有效态：统一 Reader / Writer

修改激活、成员、默认项时，先读 `services/skill_set_management_service.py`、`services/direct_activation_service.py`、`community/core/repository/implementations/skill_center/capability_desired_state.py` 和其 `tables/` 子包。

- `CapabilityDesiredStateRepository` 是 Set state、membership、Default exclusion 与 Skill/MCP Installation 的事务写入入口；表级 SQL owner 使用同一个事务 Session。
- 普通 Set 新建默认 `is_active=True`。active Set 增删成员同步维护 Installation；inactive Set 编辑不要求 Runtime 投影。
- `services/bot_capability_state_reader.py` 在读有效态前同步 Installation，然后只从 Installation 读取有效身份。Center 资产在返回前由 `SkillVersionResolver` 解析到精确 PUBLISHED Version。
- `SC_INSTALLATION_DEFAULT_SYNC_ONLY=false` 时执行完整 `flush_installations`；验收历史 backfill 后可启用 Default-only 同步模式。该模式仍同步 Default/exclusion，不等于完全取消 DB 补齐。新 Bot 的 `initialize_installations` 始终完整初始化。
- 普通 Asset、Draft、Version 查询不应为方便而触发 Bot flush。需要回答“Bot 当前应有哪些有效能力”时才使用 Reader。
- `policies/capability_ownership.py` 统一判定：Set 成员（含 inactive Set 和 excluded Default member）由 Set 控制；Direct-active 能力加入 Set 前先停用；同一 Bot 下只能属于一个 reaching Set（含 Default）。`RESOURCE_DIRECT_ACTIVE` 优先于另一个 Set 冲突。
- Default member 被 exclusion 后仍属于 Default；重新启用走 un-exclude。Default 选择统一使用 `policies/default_skill_set_selection.py`，保留全局 Default 与 engine/template 兼容规则。
- Engine/template 的代码型 Default MCP 是显式例外：`policies/platform_default_mcp.py` 管理政策事实，不能将它误认为都存在于 `ac_skill_set_mcp`。有效 MCP 还需合并政策默认项（应用 exclusion）及 active Skill dependencies；沿用 `collect_bot_active_mcps` 的统一入口。Platform Default MCP 拒绝 Direct control。

`services/_mutation_flow.py::MutationProjectionFlow` 先提交 DB，再尽力投影；Runtime 不可达、PENDING、DEGRADED 不补偿回滚已提交的 Installation。DB/权限/领域校验失败仍返回失败。响应中的 `runtime_projection` 由 `runtime_projection_contract.py` 定义，不能把接口成功解释成全部设备文件已收敛。

命令通过 `ProjectionScope` 表达变化范围；Skill claim/release 携带 MCP dependency candidates，由投影端结合完整有效集合过滤。不要把每次操作都扩大为 `everything()`。启动恢复等确实需要完整重投影的入口可使用它。

## 4. Local、Repo、Center 的内容身份

- `local://...`：Bot-owned 可变内容，由 Local upload/delete 服务及文件适配器管理；上传协议保留 raw ZIP，同时提供 multipart `files + file_paths` 文件夹上传。GET Bot Skills 的 `source=LOCAL` 仅列出该 Bot 上传资产，`active` 再筛选 Desired State；省略 source 保留完整可达资产列表。
- Local 内容替换/删除与 Skills Pool 文件迁移通过其 edit guard 协调，并有文件/DB 失败恢复逻辑。该 guard 的用途与普通 Set/Direct 命令不同；不能把 `_mutation_flow.py` 的无 Runtime 补偿规则推广到所有文件写入。
- `git://...`：Repo 内容定位；`services/git_sync.py::GitSyncService` 管理 bootstrap、周期同步、DB/缓存、OSS 散目录及下载包。改 Git 供给时同时核对 `repository_catalog_service.py` 和实际消费端，避免只验证 DB。
- `center://<skill_code>`：SC 外部定位。`skill_code` 可为普通字符串，不要求 UUID，也不等于运行时名称。
- `ac_skill.skill_uuid`：TeamClaw 内部稳定身份。Space 自建 Skill 发布时使用该 UUID 作为 SC code；Public 导入由 `public_center_identity.py` 基于 tenant/env/code 确定性派生内部 UUID。复用资产按来源身份，不能按名称复用 Local/Repo。
- `ac_skill.name`：运行时名称，允许与 SC code 不同。Canonical 内容按内部 UUID + 精确 `sc_version_number` 寻址，不使用 name、latest/current 目录。
- `SkillAssetKind.SPACE` 是当前 Center 消费分类；Space 管理权限仍需真实 Space Binding/Grant，不能从该分类推出“属于团队空间”。

## 5. Space Draft、授权与发布

修改创建/编辑时，读 `services/space_skill_application_service.py`、`draft_content.py`、`services/draft_content_store.py`、`community/core/repository/implementations/skill_center/space_skill_draft.py`。

- POST Space Skill 文件夹创建成功即持久化 V1 Draft，不是临时上传预览。Git 创建记录 branch/commit/subdir，刷新使用正式 source plugin。
- Draft 内容是不可变 revision ZIP；`ac_skill.zip_url` 保存 `draft://<uuid>/v<target_version>/<revision_id>` locator，`expected_revision_id` 做 CAS。每次编辑先写新 OSS revision，再事务切换 DB 指针；失败或被并发抢先后清理未采用 revision，旧 revision 清理是 best-effort。
- Draft 默认存储前缀来自 `DraftContentStoreConfig`，位于 `skills-upload/space-drafts`；具体 bucket/provider 由 DI 配置。活动 Draft 不靠临时 URL 的 TTL 保存。
- `ac_skill.package_url` 用于发布 staged package；Attempt 的 `frozen_draft_locator` 固定本次内容，不能用可变 Draft 替换进行中的发布输入。
- Grant 的 Owner/Manager 与 Space Membership 分别校验。编辑租约无 TTL/renew；Team 写入校验 holder/fencing token，修订冲突由 revision CAS 处理。发布入口由 Repository 在事务中协调租约和冻结，不能凭旧讨论给 Router 增加未实现的 token 参数。
- 发布成功后清除当前 Draft；升级从精确 Published Version 创建后继 Draft。`published_version_draft.py` 优先读取 Canonical Store，缺失时按 SC 精确版本恢复。
- 删除 Draft 使用现有 Repository 分支及 `deleted_scope`：Version、非 FAILED Attempt、成员/Installation、编辑工单等外部事实存在时仅清除 Draft；没有这些事实时清理自身关联并删除 Skill，允许清除仅剩 FAILED Attempt 的首次草稿。FROZEN Draft 不能删除。不要绕开检查直接删 `ac_skill`。
- Editor Request 创建 `SKILL_COLLABORATOR` Work Order；`skill_collaborator_approval_handler.py` 在审批时重新验证并写 Manager Grant。直接添加 Manager 不创建工单。

发布链路读 `services/space_skill_publication_service.py`、`services/space_skill_publication_task.py`、`publication_contract.py` 及 Publication Repository：

```text
创建/重放 Attempt → PREPARING → SC_SUBMITTING → WAITING_SC
                                            → MATERIALIZING → SUCCEEDED
                     已知失败 → FAILED；外部结果不确定 → RESULT_UNKNOWN
```

`recovery.state/kind` 决定是否自动重试或允许恢复；使用同一 Attempt 的 retry API，不把不确定的 SC 提交当成安全重发。SC accepted 不等于发布成功；Canonical 精确版本可读并持久化 PUBLISHED 后才完成发布。Track Latest 的异步投影不阻塞这一步成功。

DB 冻结/Attempt 创建与 `TaskQueueService.enqueue()` 当前不是一个跨模块事务。保留现有幂等重放和 ensure-task 恢复语义，不声称已具有 transactional outbox 或全局串行锁。

## 6. Center 物化、异步引用、巡检

`services/skill_version_materializer.py` 是 Public/Team SC 精确下载的统一物化入口。它核对 exact code/version、下载 SHA-256、包结构，调用 `validate_skill_center_exact_zip`，写并验证 Canonical Store，然后发布 Version。

SC 下载物化不调用本地 Scanner。MCP 信息来自精确下载响应的 `mcp_services`，持久化在 `ac_skill_version.metadata_json.mcp_dependencies`；不要只更新主 Skill 字段。Local/Repo 自有扫描链路仍按各自合同执行。外部 MCP 字段的 null/缺失兼容由 Gateway adapter 归一化；修改时检查 adapter 和 conformance tests。

`canonical_center_store.py` 定义精确版本 Store contract，`services/canonical_center_store.py` 提供实现；文件型 Runtime 和 Teclaw 消费同一版本内容，物理交付不同。

SC Public 引用已是持久异步批量 Operation：

1. POST 接收 `skill_codes` + Idempotency-Key，保存 batch/items，确保后台任务，返回 Accepted。
2. Worker 冻结每项外部 exact version，幂等创建/复用资产与 Version，物化 Store。
3. Ready 后调用正式 `SkillSetManagementService.add_skills`，重新验证当前权限、Set 状态和 Offline；物化成功不代表加入 Set 一定成功。
4. GET collection/detail 查询进度和逐项错误；允许部分成功。Membership 失败保留已物化的共享资产。

状态以 `reference_contract.py` 为准：QUEUED、RESOLVING_VERSION、MATERIALIZING、ADDING_TO_SKILL_SET、PROJECTING_RUNTIME、COMPLETED、FAILED。不要恢复旧的同步批量设计，也不要把 COMPLETED 自动解释为 Runtime 完全收敛。

`SkillCenterSyncService` 只巡检已物化的 Public 资产，排除 Space-bound 等不适用项；周期和手动同步复用服务。配置/周期读取 `di/modules/skill_center_group4_module.py` 和服务构造参数，文档不固定部署值。新版本走同一 Materializer 与 Track Latest，不全量导入 SC 市场。

## 7. Runtime、Track Latest 与 Service Artifact

修改投影时依次读 `services/bot_runtime_projector.py`、`runtime_resolver.py`、`services/runtime_projections/{per_domain,whole_artifact,registry}.py`。Reader 负责 DB 有效态，Resolver 负责计算，Projector/策略负责设备副作用；flush 本身没有设备 I/O。

`services/track_latest.py` 把 Version 发布转成 fanout 和逐 Bot reconcile 任务。候选 Bot 查询兼容尚未物化的历史 Set；最终有效态仍经 Reader。MCP 变化使用 claimed/released delta；投影 PENDING 可重试，DEGRADED 依当前任务语义记录后继续。Published Service Bot 的历史 Artifact 不跟随 latest。

`services/skill_symlink_listener.py` 处理设备激活和重投影事件；通过正式 Projector 恢复当前 Desired State。既有兼容 fallback 不是新增业务写入入口。

Service Bot 的边界在 `community/core/service_bot/services/`：

- `publish_flow/build_stage.py` 与 `deploy/artifact_build_request.py`：文件型 Producer 使用本次 Runtime layout observation。
- `deploy/service_skills_manifest.py`、`deploy/arca_snapshot_producer.py`、`bot_build_service.py`：捕获精确 Center 引用，排除共享仓库内容并验证 Artifact；历史重启/扩缩容/回滚消费冻结的 Artifact。
- `deploy/managed_composer.py`：`shared_corpora` 是 Snapshot 排除/历史声明，物理 Repo/Center mount 由镜像/启动脚本负责。
- Teclaw 走 Whole Artifact/StoreRef；核对 `runtime_projections/whole_artifact.py` 及对应 composer，不能把文件型路径 probe 强加给它。

Engine 拥有物理布局。Backend 通过 `community/core/skills_pool/` 的版本化合同、layout participation 和 Runtime probe 交付逻辑映射；既有路径兼容由 `path_factory.py`、factories/dispatcher 管理。新增引擎路径不得散落进 Router。Legacy/Pool 与 Center 来源正交；Center 不是切 Pool 的理由。

完整内容库与 active 发现入口分离。逐 Skill 入口可以指向 Repo/Local/Center，不能新增指向整库的 active 桥。受管链接、用户实体目录、悬空链接的降级处理以 Runtime mapping/apply contract 为准；操作结果应保留具体问题项。

## 8. Offline 与 Copy

读 `services/space_skill_offline_service.py`、`offline_policy.py`、Offline Repository，以及 `community/core/service_bot/` 的 Artifact lineage reader。

- Offline 前检查 Draft、活动 Publication、Membership、Installation 和已确认的 Service Artifact 引用，实际命令再次校验。
- 无法读取的历史 Artifact 是 warning，不作为已确认引用阻断；保留可诊断的信息，不能把 unknown 伪装成确定无引用。
- Offline 只记录离线状态并保留历史 Version，不自动生成 Vn+1 Draft，不调用 SC 删除。
- Offline 原身份不能直接升级/发布；Copy 读取选定已发布版本，创建新的 Skill UUID 和独立 V1 Draft。新副本发布使用新 UUID 作为 SC code。

## 9. DI 与 Legacy 兼容入口

装配从 `community/di/container.py` 核对：

| 模块 | 责任 |
| --- | --- |
| `di/modules/skill_center_module.py`、`skill_center_protocols.py` | Reader、UoW、Query、Direct/Set、Projector、旧 Factory/设备 dispatcher |
| `di/modules/local_skill_upload_module.py` | Local 上传依赖 |
| `di/modules/spaces_module.py`、`space_skill_repository_bindings.py` | Space Draft、Grant、Lease、Publication、Offline 服务及 Repository |
| `di/modules/skill_version_module.py` | 精确 Version 解析及 Materializer |
| `di/modules/skill_center_group4_module.py` | Reference、Sync、Track Latest 与任务注册 |
| `di/modules/skills_pool_module.py` | layout/migration/runtime 适配 |

`community/api/` 下部分 Protocol 是 core contract 的兼容 re-export。新增 `Injected` 必须验证实际 Protocol identity 在完整 composition root 中可解析，不能只测试手动构造 service。OSS、SC、Source 等外部依赖有 Community/Test/Corp provider 差异；Avernet 单测通过不证明 OCB Corp DI 已装配。

设备 I/O 的实际分流位于 `community/core/devices/services/device_filesystem_dispatcher.py`、`community/plugins/community/device_sync_dispatcher.py` 及其 DI。BaaS 可承载云端 personal/service，不能沿用旧文档“BaaS 只支持 desktop”的矩阵；业务服务使用 dispatcher contract，插件选择按实际 provider/部署形态处理。

Legacy `/api/skills`、`/api/skillsets` 位于 `community/adapters/http/skill_center/`，继续复用 Query、DirectActivation、SkillSetManagement。Legacy scope/reference resolution 与 Factory 负责旧参数、Default/exclusion、设备路径适配；保留其 wire compatibility，不恢复第二条 Installation 写路径。

## 10. 当前缺口：不得写成已交付能力

- `SkillQueryService._kind_for` 将 Center 分类为 SPACE，但 `_adapters` 中 SPACE 仍注册 `_UnavailableAssetAdapter`。这不影响 Reader 的精确 Version 解析，却意味着通用 Bot 内容解析不能仅凭路由存在就认定支持 Center；Space Version 文件读取是另一条已实现链路。
- `get_readme_by_skill` 当前支持 Repo 和 Local，Center 返回 not-found；Local 分支仍调用 `get_unique_by_id(bot_id)`，与 owner+bot 的目标约束不一致，shared default 需专项修复。文档更新不改变该行为。
- Legacy Factory/fallback、配置迁移 gate、TaskQueue 提交窗口仍存在。以上规则描述如何维护当前实现，不表示历史数据全量迁移、运行时全引擎验收或发布消费端都已经验证。

## 11. 修改后的验证与文档维护

按变化范围选择现有测试，而非每次跑 Backend 全量：

- OpenAPI：`src/backend/tests/community/adapters/http/openapi_v1/` 与 `tests/community/endpoints/test_openapi_skill*.py`。
- Desired State：`tests/community/architecture/test_installation_table_write_ownership.py`、Repository Installation 测试、Direct/Set/Reader 测试。
- Draft/Publication/Offline：`tests/community/repository/skill_center/`、`tests/community/core/skill_center/`、`tests/community/core/skill_center/services/`。
- Reference 跨模块：`tests/community/integration/skill_center/`。
- DI：`tests/community/di/test_space_skill_publication_wiring.py` 与相关 composition-root/endpoint 测试；外部 Corp provider 还需 OCB 验证。
- Runtime/Artifact：Projector 合同测试、Skills Pool 测试及 Service Bot Artifact 测试；实际 mount/软链/精确内容需设备验证。

以上测试路径除第一项外均相对 `src/backend/`。检查接口时从 Router 回溯到注入 Protocol、服务、Repository/Plugin 和错误映射；检查后台链路时再追到生命周期注册和 TaskQueue handler。列出尚未验证的外部消费者，避免以返回 200 或 Task enqueue 成功作为最终验收。

历史 Spec 和前端联调文档位于 `src/backend/specs/2026-08-20-skill-capability-upgrade/`，Installation 设计位于 `src/backend/specs/2026-08-24-installation-single-source-of-truth/`。它们用于追溯决定；旧术语、废弃状态和未实现接口须与本文件及当前代码逐项核对后再使用。
