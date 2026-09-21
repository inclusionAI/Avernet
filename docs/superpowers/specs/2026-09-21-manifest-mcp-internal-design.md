# Manifest MCP Bot 配置：内部改造方案

状态：已确认并作为当前实现基线。产品行为依据 Q1-Q13；表名与主要模块边界已按本文落地。

关联：[对外协议](2026-09-21-manifest-mcp-protocol-draft.md)、[决策记录](2026-09-21-manifest-mcp-contract-review.md)。

## 1. 基线与交付目标

- 公共实现：Avernet origin/dev `2c943c8f6fa929d02337cd3b26d0ec74fb5cfd6d`。
- 企业实现：OCB origin/dev `b04a9348e8dcbfe1018ccc9fd0f76e6fb05c633e`；gitlink `ded8c971ef380aba6676916d51f2f2cd3baf9bbf`。不能以公共 dev 已更新代替 OCB 集成验证。
- 本次通过 Git ref 只读检查；当前工作目录 dev_stable 未切换。

目标链路：Manifest 结构校验 -> apply 全类目预检 -> 比较安装及 Bot 覆盖 -> 单 MCP 事务落库 -> 按 Bot 解析有效配置 -> 既有运行时投影。用户配置修改、设备激活、重启与 whole-artifact compose 必须读到同一套 Bot 覆盖。

不新增独立定时扫描、通用重试平台或 Bot 配置公开 CRUD API；不将 Manifest 写入旧用户配置 API。

## 2. 存储：保留用户默认，新建 Bot 覆盖

建议新表 `ac_bot_mcp_config`，与安装关系使用相同的身份隔离坐标。

| 字段 | 用途 |
| --- | --- |
| id | 内部主键 |
| avernet_tenant | 租户隔离 |
| env | 系统数据隔离环境，不是 endpoint_env |
| owner_id / bot_id / server_code | Bot 与 MCP 身份，长度沿用现有安装表 |
| config | 封闭 JSON：仅 url、headers、endpoint_env、transport_protocol |
| gmt_created / gmt_modified | 时间戳，遵循现有数据库约定 |

唯一键建议 `(avernet_tenant, env, owner_id, bot_id, server_code)`，不得只按 bot_id 或 user_id 查询。实际列类型、索引长度及迁移按生产数据库和本地 SQLite 合同验证。

只保存覆盖，不保存 Center 详情、继承的用户值或已解析凭据：

```json
{"headers": {}}
```

此行明确阻断用户 Header 继承；`{}` 则表示没有任何 Bot 覆盖，可删除配置行。JSON 缺键与空字典必须一路保留，不能用 `value or fallback` 合并 headers。

旧用户表、唯一键和数据不迁移；新增表初始为空，原 Bot 默认行为不变。Bot 删除、安装移除、批量安装 flush 等路径要清理覆盖；如果 MCP 因 Skill 依赖仍有效，清除覆盖后应重投影继承配置，而不是删除仍需使用的运行时 MCP。

## 3. 解析模块：一次得到明确的端点与 Header

扩展现有 `core/mcp` 模块，提供小型 Service API；不要让每种 delivery 重写优先级。内部区分两个步骤：

1. 读取 Bot 覆盖、用户配置、Center 详情与引擎网络策略。
2. 纯计算/校验：合并声明、选择端点、覆盖 URL、组合 Header，返回结果或领域错误。

建议值类型：

| 类型 | 表达 |
| --- | --- |
| BotMcpOverride | 显式字段集合；未传与空 Header 可区分 |
| McpConfigSnapshot | 本次读取到的用户/Bot 配置，用于候选配置校验，不提供并发版本保证 |
| ResolvedMcpConfig | server_code、最终端点、最终 transport、最终静态 headers 或既有 stdio launch |
| McpConfigError | 错误类型、server_code、字段定位；不包含凭据原值 |

候选接口形状（不是现有接口）：

```text
resolve_for_bot(bot_context, server_detail)
    -> ResolvedMcpConfig

validate_override(bot_context, override, server_detail, config_snapshot)
    -> validated declaration
```

user 更新的候选值校验复用同一纯解析函数，不通过临时写库来试算；不要引入一组可选 bool 参数来区分所有入口。字段显式性由 Bot 覆盖本身决定，不能因为 fanout 传了 Python 参数就当作 Manifest 显式值。

端点选择顺序：

- 新的 Bot 显式选择严格匹配环境/协议；沿用各 Engine 现有可用网络策略及排序，在满足显式约束之后排序，不允许排序结果覆盖显式协议。
- 无 Bot 显式选择时，保留旧 user/default 兼容路径。当前逐 MCP 与 whole-artifact 的 fallback 顺序不完全相同，不能顺手统一成一个新默认。
- 基础端点选定后覆盖 URL，协议不变。后端生成的 server 静态配置不继承 user
  api_key、user/default Header 或平台托管 Secret；只保留同一 Bot override
  显式声明的非敏感 Header。
- Bot Header 整组替代 user Header；默认 Header、平台托管 Header 保留原有优先规则。Manifest 与平台托管 Header 的冲突显式拒绝，名称按大小写不敏感比较。
- 运行时 headerPolicies 不属于 YAML，且是 mcporter 全局按 host 匹配的动态注入，
  当前 server 条目没有禁用开关。后端静态字段清理不能阻止一个自定义 URL 因命中
  policy host 而获得动态 Header；发布任意 URL 能力前必须明确接受这个边界，或在
  能读取 policy host 的装配层增加拒绝规则。不能在 core 硬编码企业域名。

## 4. Manifest：schema、plan、事务与结果

`schema/entries.py` 增加封闭 config 校验，保留 schema_version 1 作为向后兼容候选方案；旧服务会拒绝新增字段，因此滚动部署期间新写入需等所有处理实例具备支持，不能声称对旧二进制双向兼容。

保存只做结构校验。apply.resolve 查询 Center、权限、平台 Header 冲突、LOCAL 限制与端点组合；所有 MCP 条目预检通过后才能进入 write。复用本次 Center 查询结果，避免一个条目在 resolve/plan 中无谓重复取源。

plan 对比规范化的覆盖声明，而不是带动态凭据的最终配置：

| 原安装 | 覆盖变化 | 结果 |
| --- | --- | --- |
| 无 | 任意 | created |
| 有 | 有 | updated（复用已有 EntryOutcome.UPDATED） |
| 有 | 无 | unchanged |
| 列表不再声明 | 任意 | 移除安装与覆盖 |

Header 名比较不区分大小写，值按原字符串比较；不对任意值 trim，也不将 URL query 随意重排。无覆盖与 config={} 归一化为无覆盖，headers={} 保留。

写入复用现有能力 Desired State 事务路径：安装表由 `tables/mcp_installations.py` 维护，新配置表增加同样接收 session 的内部 helper。一个持久化命令同时处理两者，不能先调用已提交的 activate_mcp 再单独保存 config。

已定位实际 UoW：`repository/implementations/skill_center/mcp_skill_set_control_plane.py:267-330` 的 install/uninstall 各自使用 `transactional_orm_session()`。应在这里扩展单条命令，不改成整类一次事务，以遵守 Q12 的单 MCP 原子性；不得将 SQLAlchemy session 透传给 Manifest materialiser。

建议给现有领域入口增加“设置安装及完整覆盖”的明确命令；旧 UI 安装入口保持原语义，不用一个默认 `config=None` 同时表达“保持旧覆盖”和“删除覆盖”。移除、Bot 删除及实际卸载路径必须在同一事务清理覆盖，不能由外层 HTTP router 补写。

只有事务提交后才生成投影作用域。现有 materialiser 会丢弃 activation 返回值：设计需保留 runtime 结果供报告使用。schema 错误沿用 violations；apply 保留现有 entry 形状，使用 updated 和 error/note 表达更新及未同步原因，若需机器可读 runtime 附加字段则做兼容性扩展并补测试，不将 pending 当作数据库写入失败。

## 5. 配置更新也必须进入投影

现有 ProjectionScope 只有 claimed/released/claim_all，需增加 `updated_mcp`（命名建议）。不要伪造一次卸载重装，也不要将每次更新变成全量下发。

```text
需要下发配置 = (claimed_mcp | updated_mcp) 与当前有效 MCP 集合的交集
允许删除配置 = released_mcp 减去当前有效 MCP 集合
allow-list    = 完整有效集合（不是本次变更集合）
```

扩展 DesiredStateMutation、ProjectionScope、per-domain projector、project_mcps 的合同和各实现。仅配置变化必须触发 updated；覆盖被删除但依赖仍保留 MCP 时，也要产生一次 config update。

逐 MCP 交付：先下发新增/更新配置，再声明 allow-list，最后移除不再使用的配置。当前 `skill_set_service.py:614` 注释要求撤销 allow-list 后删除，但实际实现先执行 sync_mcp_delivery（含删除）再 filter；本次受影响路径需要对齐顺序并补验证，不按注释假定现状正确。

配置解析失败时不把该 code 从期望有效集合里删掉后继续推较小 allow-list。最小安全实现是本次 MCP 投影停止并返回 pending，保留设备旧文件/allow-list；前面已成功的设备写入不伪装成原子回滚。可以保留未变的其他域投影行为。

whole-artifact 交付：collector 按 Bot 解析，将完整已解析输入交给 composer，composer 不重新选择环境/协议；仍保持现有一次 closing redeliver。任何必需 MCP 无法解析时不发删减后的 Artifact，以免“跳过失败条目”实际删除旧配置。该行为可能推迟同一个 Artifact 中其他变更的运行时生效，但 DB 事实仍保留，报告必须反映未同步。

## 6. 必须接入的入口

| 入口 | 改动 |
| --- | --- |
| Manifest apply | 安装+覆盖事务；config diff 产生 updated |
| 用户配置写入 | 候选配置逐 Bot 预检，再提交；下推逐 Bot 重新解析，不广播同一最终 payload |
| UI MCP 移除 / Bot 删除 / 安装 flush | 清理对应覆盖；依赖仍存在时刷新继承配置 |
| Skill 依赖 / 默认集合变化 | 复用有效集合守卫，不误删仍有来源的 MCP |
| DeviceActivated / RuntimeProjectionRequested | claim_all 分支读取 Bot 配置，无需新增启动专用配置源 |
| ARCA / BaaS 单 MCP 交付 | 传递已解析 URL/协议/Header，绕过旧的再次选址 |
| Teclaw/device 与 platform Artifact compose | 使用同一 Bot 解析器，保持 Engine 网络策略和交付模式 |

用户更新受影响集合以数据库安装/依赖事实判定，不以在线探测 has_mcp 决定是否校验；离线 Bot 也需纳入。遍历完整分页，不能保留当前只查 page_size=100 的截断。

Q11 预检用同一份 Center 详情分别计算旧/候选用户值：只因本次更新引入的新冲突拒绝更新；旧状态本来已失效需要单独归因，不能谎报为本次引入。Center 不可用返回无法完成校验。网络请求在短事务外完成；一期按本次读取状态校验，不新增跨入口锁或提交版本复验。

## 7. 并发与恢复：一期接受现状

用户已明确确认本期暂不处理两类并发问题：用户默认配置与 Bot 覆盖同时修改产生的校验/提交竞态，以及旧投影晚到覆盖新配置。它们是已接受限制，不再作为实施或验收前置条件。

一期不新增 owner/server 写入 guard、锁表、revision/CAS、条件回滚、投影串行化、fencing、设备版本字段或补偿调度。沿用现有 Manifest apply 锁、数据库事务与用户更新回滚行为；不移除现有保护。

Q11 的常规候选配置预检仍保留，但不保证并发请求之间的校验原子性。Q12 的单 MCP 安装关系与覆盖配置同事务仍保留，但不承诺跨请求或跨设备一致性。设备可能暂时落后于数据库，不承诺并发下自动最终收敛。

恢复保持 Q13：Manifest 的期望配置不因解析/下推错误撤销；用现有上线/重启/显式同步入口再次处理，不新增重试调度器。用户接口原有 overall success/rollback 行为保留，其旧快照回滚的并发风险本期不修复。

## 8. 模块改动地图

以下为固定 dev ref 中的源码路径，前缀为 `src/backend/src/agentclaw/community/`：

| 模块/文件 | 职责 |
| --- | --- |
| core/bot_config_manifest/schema/entries.py | config shape/字段校验 |
| core/bot_config_manifest/apply/materialisers/mcp.py | Center 预检、覆盖 diff、updated、完整覆盖写命令 |
| core/bot_config_manifest/apply/activation_delegates.py | device/platform 两种投影开关及结果传递 |
| core/models/mcp.py、新 model/DDL、core/schema.py | Bot 覆盖模型、建表注册、租户隔离 |
| core/repository/protocols/capability_desired_state.py 及实现/tables | 单 MCP 事务与清理，使用同一 session |
| core/repository/capability_desired_state_types.py | 明确配置变化 delta |
| core/mcp/services/config_service.py、新解析实现 | Bot 有效配置读取与纯解析 |
| core/mcp/config_flow.py、services/sync_service.py | 用户更新预检、完整 Bot 枚举、逐 Bot 下发 |
| core/skill_center/runtime_projection_contract.py | updated_mcp 及投影合同 |
| core/skill_center/services/runtime_projections/per_domain.py | scope 守卫和失败返回 |
| core/skill_center/services/skill_set_service.py | 配置更新/allow-list/移除顺序 |
| core/devices/services/mcp_device_payload.py、mcp_device_transport.py | 将已解析结果转换成设备格式，不重新选址 |
| core/config_compose/services/collector.py、mcporter_composer.py | Bot 解析、完整 Artifact，消除第二次选址 |
| di/modules/mcp_module.py、skill_center* | Contract 注入与测试替身装配 |

OCB：corp/core/devices/services/arca_device_sync.py、BaaS/Teclaw dispatch 及 corp/di 的 MCP/skill_center/runtime credentials 装配，沿实际调用链更新 Plugin API 实现和合同测试。Engine `/api/mcp` 已接受最终 URL/Header，优先保持外部 wire；若实际适配行为需要变更，则明确记录镜像版本与构建范围。

## 9. 可验证的实施顺序

1. 模型/DDL/读写合同：新增表、缺键与空 Header、租户隔离、无历史用户数据回填。
2. 纯解析与共享选址：Center fixture 覆盖严格/兼容模式、各 Engine 网络选择、URL override、历史 api_key 和平台 Header。
3. 单 MCP 写命令：注入安装/配置写失败，验证同事务；补全卸载清理入口。
4. Manifest schema + plan + apply：created/updated/unchanged、类目预检无写入、旧 YAML 兼容及字段错误。
5. 更新 delta + 所有 delivery 入口：config-only 更新、whole-artifact parity、启动/重启、失败时不缩减 allow-list。
6. 用户更新预检与 fanout：旧/新有效组合、离线及超过 100 Bot、旧 API rollback 合同；不新增并发一致性保证。
7. OCB 装配及端到端验收：同一 owner 两 Bot 不同 URL/Header、重启恢复、更新后真实工具调用；明确区分本地测试、CI、部署和现场验证。

部署顺序候选：先 additive DDL，再部署所有支持新协议的后端/必要适配，最后开放新 Manifest 写入。回滚到旧后端会忽略 Bot 覆盖并可能重投影用户默认值，不能将新表留存等同于功能安全回滚；回滚需要先阻止新写入并保留支持覆盖解析的服务版本，或另行制定显式配置退回方案。

## 10. 验证门禁与当前未完成项

窄测试覆盖真实业务行为及真实事务回滚，不只 mock repository：schema、materialiser、config_service、device payload、mcporter composer、runtime projections、user API、tenant isolation。协议/DI/架构测试覆盖新增 Service API 和每个受影响 Plugin Adapter，单盒 fixture 不依赖企业网络。

验证单 MCP 事务回滚、常规候选配置校验、各下发入口的 Bot 配置优先级，以及现有锁/回滚行为未被破坏；不以跨入口并发冲突或投影倒序防护作为一期验收要求。

实现时按项目规则：窄门禁 -> Standards/Spec 双轴 review -> 授权范围内提交 PR 启动 CI -> 一次本地全量与远端 CI 并行；不提前运行无关全量套件。变更文件保持低于 1000 行并更新 Context Boundary/合同文档。

本轮只完成源码核查与设计；没有执行迁移、实现代码、测试、PR、部署。平台 Header 完整保留集合与 API 返回最小扩展仍是内部设计核查项；若会改变已确认业务行为，再返回协议评审。两项并发问题已明确延期，不阻塞本期实施。
