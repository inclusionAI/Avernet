# Desktop Skill OpenAPI 能力覆盖审计

日期：2026-09-09。性质：只读源码研究与方案覆盖审计；不是实施、测试通过或上线记录。

## 结论

G1–G4 可以继续作为工程分组，当前没有证据要求新增第五个工程包；但四个名字本身不能证明 Skill 产品能力完整。正式 Spec 至少要补齐以下承接关系：

1. **G1 的 Center Query 必须承接 Bot-facing 身份解析、详情、内容、参数和 Direct 入口前置校验，不只是增加一个 Canonical `SKILL.md` 读取函数。** 当前这些 Router 都先经过 `query_service.get_skill()`；Center 在这一步就会进入占位 Adapter 并失败。共享资产需形成面向目标 Bot 的只读返回视图，不能篡改共享资产持久归属。[A: H/skills/router.py:338–470, 584–666；C/services/skill_query_service.py:142, 207–226, 409–422, 589–605]
2. **共享 README 是独立入口，不是 Bot content 的别名。** 当前它只支持 Local/Repo，Center 返回 not-found；Local 分支仍用 `get_unique_by_id(bot_id)`，没有按资产记录的 owner+bot 定位。G1 应明确维护其现有范围及 shared-default 修复；若要让无 Bot 上下文的 README 新支持 Center，需要写清可见性和版本选择，不能机械复用“目标 Bot Desired Version”。[A: H/skills/router.py:168–186；C/services/skill_query_service.py:311–360]
3. **Repo catalog/tree/detail/sync、市场 tags/sync、Space consumable/favorites、Draft/Version 文件、Grant/Lease/Publication/Offline/Copy 要在验收索引中逐项出现。** 它们大多已有共享云端业务，不应新建 Desktop 分叉。需要的是共享合同回归，以及从已发布/已引用资产进入 Desktop 的桥接验收，不是把所有云端模块重做一遍。[附录 A–E]
4. **SkillSet resources 的 CLI、Skill 依赖 MCP、直接 MCP API 是三种不同调用面。** 保持现状并验证不回归；G4 的 Skill 下载恢复不能轮询这些 Passport/MCP 入口。不要把 `clis` 展示字段解释成已经具备 SkillSet CLI 成员 CRUD，也不要把含 MCP 的旧投影改称新 MCP 持久恢复。[A: H/skill_sets/router.py:124–150；C/services/skill_set_management_service.py:803–839]
5. **配置清单是必须识别但明确不开放的相邻调用面。** 最新 `config_manifest` 的所有 Desktop construct/source cell 都拒绝；其云端 Skill materialiser 复用 LocalUpload、DirectActivation 和 Reader，属于受影响回归，不是本期 Desktop 主验收，更不能据此打开整套配置平台。独立 `cli-tools` 也是另一种二进制工具产品，不等于 SkillSet Default CLI。[A: core/bot_config_manifest/capabilities.py:325–326, 363–364；core/bot_config_manifest/apply/materialisers/skills.py:393–434；H/bots/cli_tools.py:83–185]

因此应补的是“能力 → 当前事实 → 改造/复用/排除 → 工程包 → 验收证据”的映射；不能写成“四包都做完，所以所有名字带 Skill 的功能都自动可用”。

## 基线、范围和源码坐标

- **A**：Avernet `github/dev@ca308268927f10df887ad3543d664244dbe6ce52`，本地对象库 `<Avernet research checkout>`。
- **O**：OCB `origin/dev@658abdf1cc88b047c73463463c695510866b4759`，本地对象库 `<OCB research checkout>`。
- **I**：上述 OCB 提交实际 gitlink 为 `ocb-public@137219270c46a3508daeaf92d9e660e5ee38aeb9`，由 `git ls-tree O ocb-public` 核实。A 与 I 不同，不把 A 的当前能力直接称作 OCB 已集成能力。
- 两个 checkout 的 HEAD 均不是本审计基线；源码以 `git show <sha>:<path>` / `git grep <sha>` 读取，行号属于该固定 SHA，不应按工作树当前文件跳转核对。
- **H** 展开为 `src/backend/src/agentclaw/community/adapters/http/openapi_v1`。
- **C** 展开为 `src/backend/src/agentclaw/community/core/skill_center`。
- `core/...` 展开为 `src/backend/src/agentclaw/community/core/...`。
- 附录路径统一省略公共前缀 **P = `/openapi/v1/bots`**；每个表格的源码文件均属于 A。`L` 指该文件中对应 route decorator 的起始行。
- 设计范围以本目录 `decisions.md` 的最终确认段为准，特别是 Q24/Q25/Q26/Q27 和 Q28/Q29最终决定。决策摘要与本审计是文档基线，不是代码实现证据。

主验收为 OpenClaw/Hermes Desktop；其它现有 Engine 只做受影响协议/Adapter 兼容。不会扩成完整 Bot lifecycle、Desktop Service Artifact、全量 Pool 切流、原生参数注入或新增 MCP 恢复项目。前端离线 SkillSet 禁操作保持已接受方案；Backend 不新增 Desktop 特殊拒绝语义。

研究依据包括两仓架构与模块约束；本审计仅反映固定源码，不包含业务修改或运行环境操作。

## 能力分类与 G1–G4 映射

分类：**共享** = 已有云端业务，不应复制 Desktop 实现；**设备** = 目标 Bot 设备适配及真实落地相关；**相邻/排除** = 非 Desktop 本期产品新增，仅受影响兼容或明确不承诺。下表“复用”不代表已经跑通。

| 能力/use-case | 分类及当前事实 | 工程承接 | 必须保留的验收边界 |
| --- | --- | --- | --- |
| Bot Skill 列表、source=LOCAL、active、分页搜索 | 共享 Desired State 查询；`active` 不是设备观察 | G1 回归，G4 联调 | 离线可查；LOCAL 只含 owner+bot 的上传资产，不是用户全量 Local；含 Set/Direct 可达项。[H/skills/router.py:287–335] |
| Bot Skill 详情、Center content、参数前置身份 | 共享身份/权限 + Center Query 缺口 | G1 修改；G3/G4 使用 | Center PUBLIC/Space 可见性、精确 PUBLISHED；不能照搬 Local ownership；Router 前置检查也通过。[C/services/skill_query_service.py:142, 207–226, 409–422] |
| Bot Local ZIP/目录上传、同名替换、读取、删除 | 设备；既有上传/删除服务 | G1 既有 API 回归 + G2 bytes；G3/G4 映射兼容 | 内容实际落盘才是 Local 上传成功；替换不是新增 PUT Skill；删除仍 Local-only 并保留 inactive/引用约束。[H/skills/router.py:473–581, 684–722] |
| 共享 README | Local 为设备读取，Repo 为共享读取；Center 仍缺失 | G1 单列，不能被 content 测试替代 | 无 Bot wire 与 Bot-content 的版本/权限语义不同；default 重名 owner 定位；Center 新支持范围须显式写清。[C/services/skill_query_service.py:311–360] |
| 参数 GET/PUT | 设备存储 + 共享参数定义；当前加载错误会置空 | G1 错误保护 + G2 通道回归 | 单 Skill 完整替换、保留其它项；读失败零写；写失败失败；保留旧地址/JSON，原生消费 TBD。[C/services/skill_parameter_service.py:42–103；C/factories.py:817–834] |
| Direct activate/deactivate | 共享 Desired State + 设备投影；Center 当前会被 Query 前置挡住 | G1 身份接通 + G3 单轮 + G4 恢复 | 既有 ownership/conflict/idempotency；API success 与 runtime_projection 分离。[H/skills/router.py:584–681] |
| SkillSet CRUD/activate/deactivate/Skill membership | 共享 Desired State + 设备投影 | G1 回归 + G3/G4；前端离线禁用要有明确交付项 | Default exclusion、active/inactive Set、Direct 冲突、单次最终投影；不新增 Backend 离线特殊拒绝。[H/skill_sets/router.py:81–325, 468–507] |
| Public Reference Operation | 共享异步导入/成员写入 + 设备投影 | 共享现有业务；G3/G4 接线/集成 | 创建/list/detail 都在范围；接受/物化/成员成功/设备收敛分开。内部专项另有审计，本文件不重复展开。[H/skill_sets/skill_center_references.py:131,169,209] |
| Repo 发现、内容、引用、更新 | 共享 catalog/sync + 已有 Desktop Repo downloader | G1 共享入口回归，G3 受影响映射回归，G4 全链路 | global sync 完成不等于 Desktop 本地更新；复用既有整库 Repo 同步，不改造成 Center exact 下载政策。[H/repository_catalog.py:35–120；Engine skills_repo_download.py:606–681] |
| TeamClaw/SC 市场、tags、手动 sync | 共享云端 | G1 回归、G4 取样串联；不另建 Desktop 市场 | tags/筛选、已物化资产巡检不是全市场下载；sync 与 Bot convergence 分开。[H/market/router.py:120,192,231,267] |
| Space 身份/成员、列表、创建、收藏 | 共享云端 | G1 共享回归，G4 资产供给前置 | Space-owned 不因 URL 含 bots 变成 Bot-owned；收藏不等于安装/激活；不连 Desktop。[H/spaces/router.py:212–310,690–908] |
| Space Skill identity/list/detail/consumable | 共享云端 | G1 回归 + G4 Published→引用 | Draft/identity 与可消费 PUBLISHED 区分，消费者权限不是 Owner/Manager 编辑授权。[H/spaces/skill_routes.py:114–330；C/services/space_skill_version_query_service.py:101–132] |
| 文件夹/Git 创建、Draft 文件读写、upgrade/refresh/delete | 共享云端 Draft Store | G1 共用合同回归；G4 准备资产 | 不通过 G2 的 Desktop tunnel；revision/CAS/lease 与文件校验仍归原业务。[H/spaces/skill_routes.py:147–238；H/spaces/router.py:312–488] |
| Published Version/detail/tree/file | 共享 Canonical | G1 共用回归/可复用底层，G4 版本对照 | Space Version 文件按指定 ordinal→exact 读取；不是 Bot Desired Version 或设备取证；非 UTF-8 文件仍按现有文本接口报错。[C/services/space_skill_version_query_service.py:58–99,134–154] |
| Grant/managers/owner/editor-request/lease | 共享云端协作/授权 | G1 受影响回归；不做 Desktop 版本 | 保留 Space membership 与 Owner/Manager、租约 fencing 等既有区别。[H/spaces/router.py:489–689] |
| publication-impact/create/list/detail/retry | 共享发布/任务 | G1 共享回归，G3 消费发布结果，G4 Track Latest | Attempt 恢复与 Desktop Skill 下载恢复不是同一 Task；retry 不等于再次创建发布。[H/spaces/publication_routes.py:81–223] |
| offline-impact/offline/exact-version copy | 共享资产治理 | G1 共享回归，G4 引用保护 | Skill Offline 不等于 Desktop 设备 offline；不新增桌面离线缓存销毁；copy 产生新身份/Draft。[H/spaces/skill_routes.py:420–530] |
| SkillSet MCP 权限/成员及 Skill dependencies | 相邻旧能力，与 Skill 操作组合 | 各包保现状兼容；G4 断言 Skill-only 重试零 MCP/Passport | 不新增 MCP 恢复，不承诺 V1 Link 与旧 MCP 环境原子保留。[H/skill_sets/router.py:328–465] |
| Default CLI 展示 vs 独立 cli-tools | 两种相邻旧能力 | G1 查询回归、G2 受影响通道回归；非新增 CLI 项目 | resources 里的 CLI 来自 Passport；独立二进制工具安装不证明 Default CLI 已投影。[C/services/skill_set_management_service.py:803–839；H/bots/cli_tools.py:98–112] |
| config-manifest/with-manifest | 明确非 Desktop 当前能力，云端共享 Skill 调用方 | G1/G2/G3 受影响云端回归，G4 基线核对 | Desktop 保持拒绝；不扩成整套 manifest 平台实现。[core/bot_config_manifest/capabilities.py:325–364] |

## 需要在正式 Spec 写明的缺口与防漏条件

### 1. G1 不能只修内容读取，必须走真实 Router

目前的失败链是：`GET content / parameters / Direct` → Router `get_skill` → `_resolve` → `SkillAssetKind.SPACE` → `_UnavailableAssetAdapter.resolve` → `LocalSkillNotFoundError`。Bot Skill 列表能返回 Center 不证明这些单项路由可用。Repo Adapter 已展示一种“共享持久行不变、返回视图增加目标 owner+bot”的现有模式，但 Center 仍需自己的 PUBLIC/Space 消费校验，不得直接套 Repo 的 public 判断。[A: H/skills/router.py:369–470,584–666；C/services/skill_query_service.py:142,417,557–605]

最低入口测试应同时覆盖详情/content/GET parameters/PUT parameters/Direct activate/deactivate，包含同一 Center exact 在云端与 Desktop、协作者/App grant、其它私有 Space、同名 default Bot、inactive 但可见的资产。只测 `CanonicalStore.read_version()` 或手工构造 Service 会漏掉这里的前置门禁。参数真正的设备 I/O 仍独立，不能因内容读取不需要设备就承诺参数离线可读写。

README 的默认定位风险与 Center 支持范围分开处理：前者是现有 Local 入口的 owner+bot 一致性；后者因为入口没有目标 Bot，不能隐式选择“某个 Bot 的当前期望版本”。若正式范围仅维持 Local/Repo README，应把 Center README 排除写在能力矩阵；不要宣传该共享入口已随 Bot Center content 一并支持。

### 2. G2 验收应该绑定 Local 产品流程，而不是只测隧道 echo

OCB 最新代码仍有请求/响应文本转换点：`src/desktop/bdc-crates/bdc-core-runtime/src/stages/register_to_baas.rs:814` 使用 `String::from_utf8_lossy`，`bdc-core-plugins/src/container/plugin.rs:299` 使用 `String::from_utf8(...).unwrap_or_default()`，`bdc-core-plugins/src/container/manager.rs:432` 再做响应 lossy 转换；这些点应放到双向 bytes 合同覆盖中，而不是只改 Backend 封装。[O: 上述坐标]

G2 需与既有 Local ZIP/文件夹上传、替换、二进制读取串联，覆盖 UTF-8、非 UTF-8、图片、ZIP、空文件及状态/Content-Type；以逐文件 bytes/hash 一致为验收。Space Draft 上传是 Backend→Draft Store 的共享链，不应被误接到 Desktop tunnel。Center 派生分发包是 Engine→OSS GET，也不是 BaaS 中转大包。

### 3. Repo 是已有产品供给链，不能从 Center 完成推导它完成

`POST P/skills/repository/sync` 是同步 global master fetch/DB scan/cache refresh，不是目标 Bot 的更新命令。[A: H/repository_catalog.py:103–120]

Engine 已有 Desktop `skills_repo_download.py`，在 agentbox 内启动/周期读取下载元信息，ETag 未变化不下载；公开默认缺少分发配置时直接禁用。现有内容目标按 Engine layout 选择 Pool Repo 根。故验收要分别记录共享 catalog 更新、企业分发配置、Desktop 实际仓库版本/内容、有效映射；无需另造一个 Repo 恢复系统。[A: src/engine/src/engine/community/core/skills/skills_repo_download.py:65–120,131–155,606–681]

OCB 已在 Corp DI 绑定 `ProdSkillRepoSyncPlugin`、`ProdSkillScanner`、SC Gateway 和 Space Source；不能拿 Community fake 的 catalog 或包结果当作企业端供给成功。[O: src/backend/src/agentclaw/corp/di/modules/infrastructure/corp/skill_center.py:25–78]

### 4. 离线 UI 要有交付 owner，但不要增加 Backend 政策

Q28/Q29 当前四包主要按 Backend/Desktop/Engine/任务组织，容易遗漏“前端 Desktop 离线禁止 SkillSet 变更”的已接受要求。应在 G1 或 G4 明列客户端/前端禁用与提示的交付 owner，并以现有 UI 入口核验；不能仅因为 Backend 暂时失败就称产品禁用已完成，也不能为了兜底反过来新增 Backend 的 Desktop 特殊拒绝。

这是一条既定范围的交付索引补齐，不是要求本审计修改前端，也不重开已接受的离线残留链接限制。

### 5. 最新 dev 变化：已有能力不能重复造，现有缺口不能宣称已修

- Bot Skill `source=LOCAL` 已存在且按目标 Bot 查询，正式方案应复用。[A: H/skills/router.py:307–334]
- Repo 正式 catalog/tree/detail/sync 已存在；Local raw ZIP 的正式入口是 `POST P/{bot_id}/skills`，不是重新设计 `/upload`。[A: H/repository_catalog.py:35–120；H/skills/router.py:473]
- Reference 已有 POST 202 + collection/detail，不能恢复旧同步 batch 方案。本审计只列入口，内部专项由独立审计负责。[A: H/skill_sets/skill_center_references.py:131,169,209]
- Center Query 的 SPACE 占位、README Center not-found、参数 load 吞错、Hermes local create allowlist 缺失，在 A 仍存在。[A: C/services/skill_query_service.py:142,334–340,594–605；C/services/skill_parameter_service.py:42–64；core/bot_inventory/policies/combo_policy.py:10,32–37]
- Hermes 新入口不是 DTO 枚举问题：`LocalBotCreate.engine` 是字符串，Router 交给 Workflow；Workflow `start_create` 调 `assert_local_create`，allowlist 当前只有 openclaw/claude_code。G1 修的是入口政策与对应测试，不需要重做已支持的 Desktop runtime。[A: H/local/schemas.py:23–33；H/local/router.py:189–223；core/bot_inventory/services/local_bot_workflow.py:97–110]
- A 相对 OCB gitlink I 的差异已含 `bots/config_manifest.py`、`apply/materialisers/skills.py`、`capabilities.py`、`support_matrix.py` 等。正式实现时应以匹配集成基线再核验该云端调用方，不把 I 的旧行为覆盖回 A，也不把 A 的新 Git/OSS 供给能力推广成 Desktop manifest 支持。

## 完整入口附录

下列是本范围内固定 A 的 Skill 主路由及明确相邻调用面，不是全仓 OpenAPI 清单。`共享/G1回归` 表示复用现有云端逻辑、承担相应回归，不表示要修改每一条路由。对 User/App/Delegated caller 的实际限制继续以 `H/authorization.py`、`H/admission.py` 为准，`REFUSED` 不能解释成整个产品接口关闭。正式 Router 装配及 legacy mount 见 `H/__init__.py:523–593`。

### A. Bot Skills、共享 README/兼容发布状态、Repo

源码：`H/skills/router.py`；除 Repo 表另注明外。路径均接在 P 后。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `/skills/{skill_code}/publish/status` | 兼容 SC 发布状态查询，不是 Space Attempt | 共享/G1回归 | 100 |
| GET `/skills/{skill_id}/readme` | 无 Bot 上下文的 Local/Repo README | 共享+设备/G1、G2 | 168 |
| GET `/{bot_id}/skills` | 可达 Skill 列表、LOCAL/active/keyword/分页 | 共享/G1 | 287 |
| GET `/{bot_id}/skills/{skill_id}` | Bot-facing 元数据/active | 共享/G1 | 338 |
| GET `/{bot_id}/skills/{skill_id}/content` | SKILL.md 展示 | 共享+设备/G1、G2 | 369 |
| GET `/{bot_id}/skills/{skill_id}/parameters` | 该 Bot 该 Skill 参数值 | 设备/G1、G2 | 403 |
| PUT `/{bot_id}/skills/{skill_id}/parameters` | 完整替换单 Skill 参数 | 设备/G1、G2 | 437 |
| POST `/{bot_id}/skills` | raw ZIP 创建/替换 Local | 设备/G1、G2、G3兼容 | 473 |
| POST `/{bot_id}/skills/upload-folder` | multipart 目录创建/替换 Local | 设备/G1、G2、G3兼容 | 535 |
| POST `/{bot_id}/skills/{skill_id}/activate` | Direct 激活 | 设备/G1、G3、G4 | 584 |
| POST `/{bot_id}/skills/{skill_id}/deactivate` | Direct 停用 | 设备/G1、G3、G4 | 634 |
| DELETE `/{bot_id}/skills/{skill_id}` | 删除 Local 内容/资产；非共享 Center DELETE | 设备/G1、G2、G3兼容 | 684 |

Repo 源码：`H/repository_catalog.py`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `/skills/repository` | Repo 分页/排序/筛选 | 共享/G1回归 | 35 |
| GET `/skills/repository/tree` | 共享仓库树 | 共享/G1回归 | 72 |
| GET `/skills/repository/{skill_id}` | Repo 详情 | 共享/G1回归 | 85 |
| POST `/skills/repository/sync` | global fetch/scan/cache 同步 | 共享/G1回归、G4串联 | 103 |

### B. SkillSet 与 Reference

源码：`H/skill_sets/router.py`。下表 S = `/{bot_id}/skill-sets`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `S` | Set 列表 | 共享/G1 | 81 |
| POST `S` | 创建 Set | 共享/G1 | 100 |
| GET `S/resources` | Set + MCP + Default CLI 聚合 | 共享/相邻/G1 | 124 |
| GET `S/{set_id}` | Set 详情 | 共享/G1 | 153 |
| PUT `S/{set_id}` | Set 元信息更新 | 共享/G1 | 174 |
| DELETE `S/{set_id}` | 删除 Set 与相应生效变更 | 设备/G3、G4 | 198 |
| GET `S/{set_id}/skills` | Skill 成员列表 | 共享/G1 | 219 |
| PUT `S/{set_id}/skills/{skill_id}` | 已有资产加入 Set | 设备/G3、G4 | 250 |
| DELETE `S/{set_id}/skills/{skill_id}` | 移除/Default exclusion | 设备/G3、G4 | 289 |
| GET `S/{set_id}/mcps` | MCP 成员/依赖视图 | 相邻/G1回归 | 328 |
| GET `S/{set_id}/mcp-permissions` | 检查 MCP 使用权限 | 相邻/G1回归 | 349 |
| POST `S/{set_id}/mcp-permission-requests` | MCP 权限申请 | 相邻/保持现状 | 373 |
| PUT `S/{set_id}/mcps/{server_code}` | 添加 MCP | 相邻/保持现状 | 402 |
| DELETE `S/{set_id}/mcps/{server_code}` | 移除 MCP | 相邻/保持现状 | 435 |
| POST `S/{set_id}/activate` | Set 激活 | 设备/G3、G4 | 468 |
| POST `S/{set_id}/deactivate` | Set 停用 | 设备/G3、G4 | 489 |

源码：`H/skill_sets/skill_center_references.py`。R = `S/{set_id}/skill-center-references`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| POST `R` | 带 Idempotency-Key 创建异步 Public Reference | 共享+设备/G3、G4 | 131 |
| GET `R` | Reference Operation 分页列表 | 共享/G1回归、G4 | 169 |
| GET `R/{reference_id}` | Operation 与逐项结果 | 共享/G1回归、G4 | 209 |

### C. 市场、Space 身份/协作与收藏

源码：`H/market/router.py`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| POST `/market/skills` | TeamClaw 市场查询 | 共享/G1回归 | 120 |
| POST `/market/mcp-servers` | MCP 市场查询 | 相邻/保持现状 | 143 |
| POST `/market/skill-center/skills` | SC Public 市场查询 | 共享/G1回归 | 192 |
| POST `/market/skill-center/sync` | 已物化 Public 资产巡检 | 共享/G1回归、G4串联 | 231 |
| GET `/market/skill-center/tags` | SC tags 初始化筛选 | 共享/G1回归 | 267 |

源码：`H/spaces/router.py`。T = `/spaces/{space_id}`。本表只列身份/成员/收藏，Draft/Grant/Lease 见下一节。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `/spaces` | 可访问 Space 列表 | 共享/G1回归 | 212 |
| POST `/spaces/personal/initialize` | 初始化个人 Space | 共享/G1回归 | 244 |
| POST `/spaces/create` | 创建团队 Space | 共享/G1回归 | 266 |
| GET `T/members` | 成员列表 | 共享/G1回归 | 285 |
| POST `T/members` | 添加成员 | 共享/G1回归 | 690 |
| DELETE `T/members/{member_user_id}` | 移除成员 | 共享/G1回归 | 718 |
| PUT `T/members/{member_user_id}/role` | 更新成员角色 | 共享/G1回归 | 748 |
| POST `T/market-favorites` | 收藏指定市场目标 | 共享/G1回归 | 785 |
| POST `T/market-favorites/cancel` | 取消收藏 | 共享/G1回归 | 817 |
| POST `T/market-favorites/search` | 收藏列表/筛选 | 共享/G1回归 | 848 |
| POST `T/market-favorites/status` | 批量查询收藏状态 | 共享/G1回归 | 881 |

### D. Space Skill、Draft、版本、Grant、Lease

源码：`H/spaces/skill_routes.py`。K = `T/skills/{skill_id}`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `T/skills` | Space Skill 列表 | 共享/G1回归 | 114 |
| POST `T/skills` | 文件夹创建身份与 V1 Draft | 共享/G1回归 | 147 |
| POST `T/skills/import-from-git` | Git 创建身份与 Draft | 共享/G1回归 | 203 |
| GET `T/skills/consumable` | 可消费 Published 列表 | 共享/G1、G4桥接 | 239 |
| GET `K` | Skill 身份/详情 | 共享/G1回归 | 275 |
| GET `K/versions` | Published Version 列表 | 共享/G1回归 | 298 |
| GET `K/versions/{version}` | 指定 Version 详情 | 共享/G1回归 | 331 |
| GET `K/versions/{version}/files` | 精确版本文件树 | 共享/G1回归 | 360 |
| GET `K/versions/{version}/files/{path:path}` | 精确版本文本文件 | 共享/G1回归 | 389 |
| GET `K/offline-impact` | 下线影响面 | 共享/G1回归 | 420 |
| POST `K/versions/{version}/copy` | 从下线已发布版本复制新身份/Draft | 共享/G1回归 | 454 |
| POST `K/offline` | Skill 下线 | 共享/G1回归 | 490 |

源码：`H/spaces/router.py`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `K/draft/files` | Draft 文件树 | 共享/G1回归 | 312 |
| GET `K/draft/files/{path:path}` | Draft 文本文件读取 | 共享/G1回归 | 336 |
| PUT `K/draft/files/{path:path}` | Draft 单文件写入/CAS | 共享/G1回归 | 364 |
| POST `K/draft/upgrade` | 已发布版本建立后继 Draft | 共享/G1回归 | 397 |
| POST `K/draft/refresh-from-git` | Git 刷新 Draft | 共享/G1回归 | 424 |
| DELETE `K/draft` | 删除 Draft/按既有条件清理身份 | 共享/G1回归 | 451 |
| GET `K/grants` | Owner/Manager grants | 共享/G1回归 | 489 |
| PUT `K/managers/{manager_user_id}` | 添加 Manager | 共享/G1回归 | 510 |
| DELETE `K/managers/{manager_user_id}` | 移除 Manager | 共享/G1回归 | 534 |
| POST `K/owner-transfer` | 转移 Owner | 共享/G1回归 | 558 |
| GET `K/draft/lease` | 查询编辑租约 | 共享/G1回归 | 583 |
| PUT `K/draft/lease` | 获取编辑租约 | 共享/G1回归 | 600 |
| DELETE `K/draft/lease` | 释放编辑租约 | 共享/G1回归 | 617 |
| POST `K/draft/lease/takeover` | 接管编辑租约 | 共享/G1回归 | 640 |
| POST `K/editor-requests` | 编辑协作权限申请 | 共享/G1回归 | 657 |

不存在因为 Desktop 而需新增的 Draft replacement、独立 consume 或 package 下载 OpenAPI；本审计没有在上述正式 Router 中找到这些独立 endpoint。`consumable` 是候选资产查询，消费行为通过现有 Set/Direct 和 Runtime 完成；原生脚本执行不在此 HTTP 清单中。

### E. Publication

源码：`H/spaces/publication_routes.py`。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `K/publication-impact` | 发布影响面 | 共享/G1回归 | 81 |
| POST `K/publications` | 创建/重放发布 Attempt | 共享/G1回归、G4 Track Latest桥接 | 116 |
| GET `K/publications` | Attempt 列表 | 共享/G1回归 | 143 |
| GET `K/publications/{attempt_id}` | Attempt 详情/恢复指引 | 共享/G1回归 | 171 |
| POST `K/publications/{attempt_id}/retry` | 同 Attempt 重试/恢复 | 共享/G1回归 | 197 |

### F. 相邻 MCP、独立 CLI 与配置清单

源码：`H/mcp/router.py`。保持现有能力；只覆盖 Skill 组合与受影响通道，不承诺新 MCP 恢复。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| GET `/{bot_id}/mcps` | Bot MCP 列表 | 相邻/G1回归 | 133 |
| POST `/{bot_id}/mcps/{server_code}/activate` | Direct MCP 激活 | 相邻/保持现状 | 161 |
| POST `/{bot_id}/mcps/{server_code}/deactivate` | Direct MCP 停用 | 相邻/保持现状 | 190 |
| GET `/mcp/servers` | MCP server 目录 | 相邻/保持现状 | 342 |
| GET `/mcp/tenants` | MCP tenant 目录 | 相邻/保持现状 | 386 |
| GET `/mcp/servers/{server_code}` | Server 详情 | 相邻/保持现状 | 404 |
| GET `/mcp/servers/{server_code}/permissions` | 权限检查 | 相邻/保持现状 | 430 |
| GET `/mcp/servers/{server_code}/config` | 用户 MCP 配置 | 相邻/保持现状 | 473 |
| PUT `/mcp/servers/{server_code}/config` | 替换用户 MCP 配置 | 相邻/保持现状 | 497 |

源码：`H/bots/cli_tools.py`。与 Passport Default CLI 不合并；不新承诺 Desktop 工具安装上线。

| Method / path | 用例 | 分类 / G | L |
| --- | --- | --- | --- |
| POST `/{bot_id}/cli-tools` | pinned 二进制工具安装 | 相邻/仅受影响回归 | 83 |
| GET `/{bot_id}/cli-tools` | 平台安装记录列表 | 相邻/仅受影响回归 | 131 |
| DELETE `/{bot_id}/cli-tools/{name}` | 删除工具 | 相邻/仅受影响回归 | 157 |

配置清单的 Desktop 拒绝来自领域 capabilities，不应修改。下列仅说明为什么它是共享服务变更的云端消费者。

| Method / path | 用例 | 源码 / L |
| --- | --- | --- |
| GET `/{bot_id}/config-manifest` | 读取配置文档 | `H/bots/config_manifest.py:70` |
| PUT `/{bot_id}/config-manifest` | 校验/存储并启动 apply | 同文件 `101` |
| DELETE `/{bot_id}/config-manifest` | 清除声明，不删除既有资产 | 同文件 `181` |
| GET `/{bot_id}/config-manifest/capabilities` | construct/source 支持矩阵 | 同文件 `209` |
| POST `/{bot_id}/config-manifest/apply` | dry-run/异步 apply | `H/bots/config_manifest_apply.py:86` |
| GET `/{bot_id}/config-manifest/applies/{apply_id}` | 一次 apply 报告 | 同文件 `206` |
| GET `/{bot_id}/config-manifest/last-apply` | 最近 apply 报告 | 同文件 `243` |
| POST `/with-manifest` | 云端 Bot+manifest 创建组合 | `H/bots/create_with_manifest.py:129` |
| GET `/{bot_id}/with-manifest/status` | 组合创建进度 | 同文件 `244` |

原生参数消费：不是上述参数 GET/PUT 的实现承诺。只验收当前存储/回读和错误正确性；自动注入 prompt/env、Hermes 原生消费、Skill 脚本读取参数需要独立证据，未补证前保持 TBD。

### G. Deprecated Skill OpenAPI 与创建前置入口

源码：`H/deprecated/skills.py`。这些 route 通过 `legacy_route()` 注册，不会出现在仅扫描 `@router.get/post` 的清单里。它们是兼容面，不重新成为 Desktop 新合同。

| Method / path | 用例 | L |
| --- | --- | --- |
| GET `/skills` | 旧 query bot_id/owner_entity_id 列表 | 190 |
| POST `/skills/upload` | 旧 query Bot raw ZIP 上传 | 201 |
| GET `/skills/{skill_id}` | 旧 Local 身份反查 Bot 的详情 | 324 |
| DELETE `/skills/{skill_id}` | 旧 Local 删除 | 333 |
| POST `/skills/{skill_id}/activate` | 旧 Local Direct 激活 | 342 |
| POST `/skills/{skill_id}/deactivate` | 旧 Local Direct 停用 | 351 |

本期主范围另包含 `POST P/local` 的 Hermes 创建入口，源码 `H/local/router.py:189`，政策缺口见上文。其它 Local Bot list/devices/files/authorization/lifecycle 不因本次审计一并成为完整验收项目；只验证初始化、绑定和启动恢复所必需的路径。

Legacy `/api/skills`、`/api/skillsets` 属于 BFF 兼容面，不属于 P OpenAPI 附录，但 G1 Query/参数和 G3 共享投影改变时需要相关 endpoint 回归。通用文件 API、资源 API、Engine `/api/file/*` 是 G2 的依赖通道，亦不是新增 Skill 产品 endpoint。

## 后续验证建议与证据边界

1. G1：真实 OpenAPI Router→DI→Query 的 Center 权限/身份/content/参数/Direct 窄合同测试；Local/Repo/README/default/legacy 回归；Hermes create 政策测试。不要用手工拼 Service 绕过 Router grant。
2. G2：OCB 双向 bytes 协议测试 + Local ZIP/目录上传/替换/读取逐文件 hash；保持 JSON/health 旧调用兼容。
3. G3：一次 apply 的 mixed Local/Repo/Center、精确版本缓存、MOUNT/DOWNLOAD、部分 PENDING、显式 retired/实体保护；云端与旧端合同组合回归。G3 单独通过不等于自动最终收敛。
4. G4：把上述产品入口连到实际 Desktop，并验证 Reference/Space Publication/Track Latest/Direct/Set 各条触发；首次冷包、重启、版本更新、取消、重绑定、错过唤醒和 finite-task 过期；Skill-only 重试零 MCP/Passport；前端离线禁用交付。共享 Space 辅助入口按变更风险回归，不要求每次都操作真实外部发布。
5. 两仓集成：除了 A 的合同测试，还需匹配 OCB Corp DI/供给/存储/隧道，记录新的 gitlink、客户端与 Engine 版本。OCB 当前 DeviceSync 对 Desktop 使用 `DesktopBaasInvokeTransport`，其它 BaaS 使用 `BaasInvokeTransport`，不能用 provider=baas 一条假设代替两种传输验收。[O: src/backend/src/agentclaw/corp/di/modules/infrastructure/corp/device_sync.py:114–138]

本审计只证明固定源码的入口和调用关系，以及方案仍需明写的覆盖项；没有执行单测、CI、评审、合并、部署或真实设备操作。这些阶段均保持未验证。
