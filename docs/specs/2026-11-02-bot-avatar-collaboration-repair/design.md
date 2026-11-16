# Bot 头像同步与协作信息手动修复设计

- 日期：2026-11-02。
- 状态：设计稿；需求边界已确认，两个独立实施 plan 已编写（见 §9），尚未实施。
- 所属模块：BCS、Backend；前端是查询与修复接口的消费者，本次不实现前端按钮。
- 代码阅读基线：`a9b62a1f2`。
- 实施交付：[BCS plan](bcs-plan.md) 与 [Backend plan](backend-plan.md) 分别执行；本文是共同契约，不是混合实施计划。

## 1. 需求与已确认决策

1. Backend 的 Bot 头像同步到 BCS，在 `bcs_bots` 独立列 `avatar_url` 保存。
2. 只有 BCS V1 Group/Session 查询输出头像；不扩展 legacy 查询、消息历史或 WS 消息协议。
3. 日常注册、名称/描述/头像更新复用 Backend 现有注册和资料更新链路。
4. 不新增自动重试、outbox、持久化补偿任务、定时扫描或一致性保障逻辑。
5. Backend 提供前端手动触发的单 Bot 修复接口：

   ```http
   POST /openapi/v1/bots/{bot_id}/collaboration/repair
   ```

6. 修复以 Backend 当前已提交事实为来源，覆盖名称、描述、头像、Owner、协作者，以及本文明确列出的其他已有同步资料。
7. **BCS 对应 Bot 不存在时返回明确错误，不自动注册、不创建、不恢复已删除 Bot。**
8. 修复在当前请求内执行并返回实际分项结果；“已入队”不等于“已修复”。
9. BCS 和 Backend 变更分别规划、实现、测试。部署先 BCS，后 Backend。

## 2. 依据与现状

### 2.1 架构与模块契约

- [架构宪法](../../arch/arch.rules.md)、[CI 约束](../../arch/ci.enforce.md)。
- [Backend Context Boundary](../../arch/context-boundary-format.md)、[Plugin 契约测试](../../arch/protocol-contract-tests.md)。
- [领域入口](../../../CONTEXT-MAP.md)、[ADR 目录](../../adr/)。本设计不改变已有 Owner/manager 权威边界；未发现直接约束本需求的已接受 ADR，不新增或替换 ADR。
- [BCS 本地规则](../../../apps/bcs/AGENTS.md)、[分层说明](../../../apps/bcs/CLAUDE.md)。
- [BCS V1 OpenAPI](../../../apps/bcs/api-contracts/v1/openapi.yaml)、[共享模型](../../../apps/bcs/api-contracts/v1/domain-models.yaml)。
- [BCS Bot Authority](../../../apps/bcs/api-contracts/v1/openapi/bot-authority.yaml)。
- [Backend managers 同步](../../../apps/backend/specs/2026-10-27-bcs-manager-sync/spec.md)、[Owner 同步](../../../apps/backend/specs/2026-11-01-bcs-ownership-sync/spec.md)。
- [Backend 地址解析](../../../apps/backend/src/agentclaw/community/core/bot_management/addressing.py)、[Caller identity 边界](../../../apps/backend/src/agentclaw/community/core/caller_identity/README.md)。

本文扩展的是头像字段和新的手动修复用例，不替代上述既有契约。特别是：现有异步 Owner/managers 同步的准入、有限重试、生命周期与 missing-Bot no-op 语义仍保留；新修复用例对 no-op 作更严格的结果解释。

### 2.2 代码阅读结论

| 位置 | 当前行为与缺口 |
| --- | --- |
| `Backend .../openapi_v1/desktop_metadata.py` | 头像更新通过 `update_bot(ext={"avatar_url": ...})`，空字符串表示清空 |
| `Backend .../bot_management/services/bot_service.py` | `update_bot()` 仅名称或描述更新时调用 `_sync_bot_to_bcn()`，仅改头像不会触发 |
| `Backend .../bot_management/services/bcn_service.py` | `onboard_bot()`、`register_provider_bot()` 未发送头像；现有方法可能返回 skipped 或非成功业务结果 |
| `BCS .../bcs-bot/src/application/onboarding.rs` | admin onboard 在 Bot 不存在时返回 `onboarded=false`；不是 HTTP 错误，也不等同注册成功 |
| `BCS .../bcs-bot-store/src/persistent_repo/mod.rs` | 通用 `save_to_db()` 可 UPDATE 或 INSERT；仅做存在性预检查不足以证明修复绝不会创建记录 |
| `BCS .../application/v1/group.rs` | 详情有参与者；普通列表只有数量、driver，DM 列表另有 `peer_actor` |
| `BCS .../application/v1/session.rs` | 详情有参与者；列表只有参与者数量等摘要 |
| `BCS .../core/registry.rs` | 名称补齐逐个调用 registry，且只补缺失名称；不能直接当作“最新头像批量查询” |
| `BCS .../bcs-app-group/src/projections.rs` | 当前 `project_summary()` 也会调用 `backfill_bot_names()`，用于既有 driver/DM peer 名称；新增列表成员名称不能因此隐式改成 registry 当前名称 |
| Backend `ownership_sync/repair.py`、`manager_sync/repair.py` | 内部修复仅提交内存队列，返回准入结果，不提供前端所需的执行完成结果 |

上述路径中的省略部分以本文引用的模块与基线为准。Backend `onboard_bot()` 的旧说明称可创建记录，与当前 BCS admin onboard 的缺失行为不一致；实施时校正相关说明，不把旧注释作为自动注册依据。

## 3. 总体方案与非目标

```text
日常注册/资料编辑
  Backend 已提交 Bot 资料
    → 现有注册/资料更新请求（增加 avatar_url）
    → BCS application → core → repository → bcs_bots.avatar_url

V1 Group/Session list/get
  既有鉴权、资源查询及分页
    → 当前结果集参与者 ID 去重
    → BCS 本地批量读取展示资料
    → application 投影 → V1 HTTP 响应

前端手动修复
  Backend 鉴权及稳定地址解析
    → 确认 BCS 对应 Bot 已存在
    → 按现有规则读取事实、执行一次同步、返回分项结果
```

头像是当前 Bot 展示资料，不是创建 Group/Session 时的历史快照。只保存 URL，不下载、代理、生成或重新上传图片；BCS 查询不访问 Backend。

不把头像复制到各 Group/Session 的持久化成员记录，不增加一套头像缓存或刷新任务。不扩展 Human 头像同步；Human 参与者本次输出 `avatar_url: null`。没有头像时由前端沿用自己的默认头像策略。

不实现批量修复、自动回填、修复任务查询、跨请求幂等记录、跨副本串行化或跨服务事务。存量 Bot 可通过后续正常资料同步或显式单 Bot 修复补齐头像。

## 4. BCS 数据与写入契约

### 4.1 存储

`bcs_bots` 新增 `avatar_url TEXT NULL`，默认 NULL，不建索引。头像不参与检索或授权，不写入 `bot_info`、capabilities JSON 或 `friend_ext`。

- SQLite、MySQL 各增加一个新 migration，编号取实施时该方言最大版本之后的空闲编号。
- 不改历史 migration、基线 DDL、已部署版本记录及 checksum。
- SQL repository、memory/file 持久化实现、Bot 资料读取和缓存映射保持同一含义；旧序列化数据缺少字段时视为无头像。
- 只需新增 schema，不从 Group/Session 或其他 JSON 猜测历史头像。
- 落库失败返回错误；缓存仅在成功提交后更新或失效，不出现“缓存新值、数据库旧值”的成功返回。
- 审核其他 Bot 保存路径，确保省略头像的重连、onboard、名称更新或状态更新保留原列值。

### 4.2 输入与清空语义

在 Backend 实际使用的既有 Provider 注册和 admin 资料更新请求上增加 `avatar_url`：

| 输入 | 新建 Bot | 更新已有 Bot |
| --- | --- | --- |
| 未提供字段 | 无头像 | 保留原值 |
| 非空字符串 | 保存 URL | 替换 URL |
| `""` | 无头像 | 清空为 NULL |
| 显式 JSON `null` 或非字符串 | 拒绝非法输入 | 拒绝非法输入 |

输入的“未提供”与“显式 null”需要在解析边界区分，不能用默认 `Option<String>` 把它们无意合并。领域更新表达为保持/清空/设置，不以任意空值猜测调用意图。

输出没有头像时显式返回 `null`。旧客户端不发送字段仍正常工作。已有 URL 来源及校验规则保持兼容；不额外要求可匿名访问，不探测 URL，也不记录可能携带签名的完整 URL。

资料更新沿用 BotCore 的 Provider 父身份保护：未提供的 `agent_code` 不清除已保存的父身份，显式换绑仍拒绝。若身份校验期间目标已删除，existing-only 入口仍返回 missing；确认目标的读取失败必须继续作为错误传播。

Provider 注册的 A2A Card URL 与内联 Card 分支遵守相同头像输入语义。ownership/Card 原子注册成功后，显式设置或清空经现有 registry core 写入；省略头像不新增写入。头像写入失败或目标已删除时返回错误，保留已提交的注册事实，不新增回滚或自动重试，也不返回成功凭证结果。A2A 重复注册原有 conflict 规则保持不变。

日常注册允许按原业务创建 Bot；**手动修复绝不能调用注册、ensure、恢复或会 fallback 到创建的路径**。资料修复必须走已有记录更新语义，在 repository 写入时检查记录存在且未删除；并发删除时返回 missing，不能在预检查后再走通用 upsert。若现有 admin 更新路径尚不能满足这一点，BCS plan 在该更新分支补齐条件更新，不改变正常注册语义，不新增修复专用 HTTP 接口。

## 5. 仅 V1 的查询输出

### 5.1 覆盖接口

以 BCS V1 OpenAPI 的 public 路径为准；同一 application 投影的已存在内部 V1 映射应保持一致，不新建路由别名。

| 接口 | 新增输出 |
| --- | --- |
| `GET /openapi/v1/collaboration/groups` | 每项增加轻量 `participants` |
| `GET /openapi/v1/collaboration/public-groups` | 复用 GroupSummary 的同一字段，仍遵守公开 Group 的既有可见性规则 |
| `GET /openapi/v1/collaboration/groups/{group_id}` | 现有 `participants[]` 增加 `avatar_url` |
| `GET /openapi/v1/collaboration/groups/{group_id}/sessions` | 每项增加轻量 `participants` |
| `GET /openapi/v1/collaboration/sessions/{session_id}` | 现有 `participants[]` 增加 `avatar_url` |

DM 的 `peer_actor` 同时增加 `avatar_url`，不改变其存在条件。V1 create/update/成员操作如复用同一个详情或 Participant 响应模型，也使用相同头像字段语义；文档与序列化测试覆盖这些直接传播面，不另行改动其业务行为。

Legacy 响应保持原形状。不能仅给跨版本共享的序列化类型加字段，导致旧 HTTP 或 WS 输出自动增加头像。需要分离存储资料、application 展示投影与旧版 DTO。

### 5.2 列表参与者摘要

新增轻量类型，字段固定为：

```json
{
  "actor_id": "bot_example",
  "actor_kind": "bot",
  "name": "研发助手",
  "avatar_url": null
}
```

- `actor_id`、`actor_kind` 保留原成员身份；`name` 与 `avatar_url` 为 nullable 展示字段。
- **列表轻量摘要的 `participants[].name` 来自该 Group/Session 成员记录中已有的 `bot_name` 快照，原样返回且允许 null；不为这个新字段新增名称查询或 registry 补齐。** 即使 registry 有当前名称，缺失快照仍返回 null，已有快照也不被当前名称覆盖。
- `participants` 始终是数组，按原成员顺序返回全部轻量摘要，不静默截取前 N 人。
- 不返回详情中的 role、mode、message_view_scope、tags 等控制信息。
- 现有 `participant_count`、driver 和 DM 字段的含义不变。
- DM 的两个成员行同样出现在新 `participants` 摘要中，对已持有读取权限的会话相关查看者可见；`peer_actor` 的省略只是既有展示惯例，不是可见性边界。
- Group 使用 Group 自己的成员，Session 使用 Session 当前成员，包括会话独有的动态成员。
- 对可见资源投影展示资料，不因为头像查询将不可见 Bot、其他租户资料或不可见成员扩展到结果中。公开列表必须测试 Human/受限成员的既有可见性边界，不能借共享 DTO 绕过成员披露规则。

详情保留原 Participant 的全部字段，只增加 `avatar_url`；详情名称仍遵守既有补齐规则。当前 Group 的 `project_summary()` 已为 `driver_bot_name` / DM `peer_actor.name` 调用名称补齐，这些既有字段的行为保持不变；新增 `participants[].name` 必须在该补齐修改成员副本之前提取快照。Session 列表直接取本 Session 成员快照，不新增名称补齐。头像则始终来自本次头像批量读，因此名称快照和当前头像可以独立变化。

### 5.3 头像读取与访问成本

在 application 完成原有鉴权、过滤、分页后，按当前页所有 Bot 参与者去重，通过 core 的展示资料批量契约查询；core 使用 repository port，adapter 不查库。

- 建议批次上限 256 个 ID，顺序或有界并发，不创建每成员一个任务。
- 只读取 ID、头像及必要的展示/隔离字段，不为头像加载 token、完整 capabilities 或运行时连接。
- 设当前页参与者出现次数为 P、不同 Bot 数为 U，本次列表扩展唯一新增的数据库读取是头像查询，至多 `ceil(U / 256)` 批、读取至多 U 条匹配资料，投影成本 O(P)；新增成员名称只读已取得的快照，不增加查询。此预算不包含且不要求删除原有权限、资源、计数以及 Group driver/peer 名称补齐等查询；测试应分别记录既有读取与新增头像读取。
- 保留现有分页上限，不进行全库 Bot 扫描。结果大小随 P 增长；实现验证应包含最大允许页面及高成员数，不声称小样本证明了容量。
- 优先使用直接、带作用域的轻量批量读；如复用现有缓存，必须证明缓存键、更新失效与新字段加载完整，不新增第二份缓存。
- 新头像查询每次覆盖响应字段，包括 NULL；不能套用“仅当名称缺失才查询”的分支。
- 无头像或身份确实缺失时返回 NULL。对保留成员的软删除 Bot，可按既有历史展示边界读取 tombstone 头像；不能恢复成员权限或 Bot 状态。
- 数据库查询失败不能伪装成“所有头像为空”；保留可失败契约并映射服务错误。

## 6. Backend 日常同步

注册继续调用现有 `register_provider_bot()`，资料更新继续调用现有 `onboard_bot()`/资料更新通道。头像加入相同 payload，不创建新的头像同步 HTTP 通道。

- 创建、启动、重启等原本会注册的实际入口，携带当前已保存头像；不新增生命周期触发点。
- `update_bot()` 的同步触发条件包含显式头像设置/清空；任意其他 ext 变化不自动扩大同步范围。
- 从已持久化资料读取最终名称、描述、头像，避免请求的局部 ext 覆盖已有资料；显式清空优先于旧字段回退，不能因 `value or legacy_value` 复活旧头像。
- 名称/描述未变而头像改变，仍发送；已有 Bot 不因缺字段的旧调用而丢失头像。
- 日常资料同步仍 best-effort，不把 BCS 故障变成新的 Backend 编辑事务失败条件；Backend 自身写入失败始终报错。
- 当前同步方法返回 skipped/onboarded=false 时不得误记“已同步成功”；手动修复还需按下一节返回明确结果。
- 不新增重试。既有 Owner/managers 后台同步的有限重试、队列和生命周期不变。

## 7. 手动修复接口

### 7.1 请求、权限与寻址

```http
POST /openapi/v1/bots/{bot_id}/collaboration/repair
```

请求无需业务 body；不提供字段选择、owner 覆盖、协作者覆盖、BCS ID、目标环境、force-create 等参数。兼容既有 Bot 定位规则，地址有歧义时返回冲突，不猜测目标。

- 复用 OpenAPI 当前认证、App grant、Bot admission 与 owner-scoped 授权链，只有 Backend 当前 Owner 可执行。
- Backend 授权、租户及存活状态校验在任何 BCS 请求之前完成；不能依赖可能已漂移的 BCS owner 判断调用人是否有权修复。
- 由 `BotAddressResolver` 解析唯一 Bot，携带稳定 `bot_pk`、`entity_id`、租户与环境。
- BCS 地址复用既有 `bcn_bot_reference`：原生 service Bot 使用 `bot_id:entity_id`，转换后的 service Bot 保留 `ext.bcn_provider_bot_ref`，personal/default Bot 沿用既有 Owner 后缀规则。三个分项使用同一已注册地址；service Bot handoff 不得用当前 Owner 重新拼接地址。
- 复用既有下游鉴权：Owner/managers 使用受 scope 限制的 Backend trusted App principal，资料使用既有认证链。不要求 BCS 漂移后的旧 Owner 授权，也不放宽 BCS 的 trusted-App/Provider scope。
- 只发单 Bot 操作，不启动/重启 Bot，不签发或轮换凭证，不写 Backend 的 Owner/协作者业务事实。

### 7.2 修复字段清单与权威边界

“已经同步的信息”不是任意复制 BCS 整条记录，而是重放有 Backend 权威事实的既有同步投影。

| 分项 | 权威来源与动作 | 约束 |
| --- | --- | --- |
| `profile` | 当前 Backend 名称、描述、头像，经现有资料更新通道覆盖 | 空头像清空；不用默认空值掩盖读失败 |
| `ownership` | 当前已提交 `owner_id`，复用 ownership reader/projector 和单次发送 Plugin | 不是 handoff；不走转交申请/接受，不初始化 version-0 ownership |
| `managers` | 当前有效 admin/member 协作者，复用 managers reader/projector 和全量替换接口 | 保留 Space 资格规则、排除 Owner、去重排序及 1,000 人限制；移除已撤销成员 |

现有 onboard 协议将空串/纯空白简介解释为保留原值，响应也不回传简介。
2026-11-05 更新：收到有效 onboard 成功确认后，这类 `profile` 返回
`succeeded`，不再因简介为空而返回 `unknown`。成功表示既有资料更新操作
已确认，不承诺清空 BCS 简介；BCS 原有 Keep 语义不变。实际异常、缺失目标
及无效确认仍按既有失败/未知规则处理。

修复范围仅为 `ownership`、`profile`、`managers`，响应固定为该顺序的三项。
`caller_config` 不再作为修复分项，不读取或验证 call type/revision，也不发送
caller 配置。MCP/CLI 调用身份模式更新时的正常 caller 同步链路保持不变。
这是四项到三项的有意响应契约调整，前端不得再要求第四项。

以下不是可从 Backend 直接重放的 Bot 基础资料，禁止盲目覆盖：

- `visibility`、`user_visibility`、`friend_ext`、`friend_check_in_strategy` 中的审批/关系状态：现有 `bot_public` 流程会读取 BCS 并按审批结果修改；不能把“有 PATCH 调用”当成 Backend 有完整权威快照，修复不能重新发起或重放审批。
- `task_claim_mode`、`task_dream_mode` 等 BCS 自身控制面状态，没有本文确认的 Backend 权威快照，不按缺省值重置。
- Provider/connection mode、webhook、凭证、在线状态、所有权内部版本、Group/Session、好友关系、Space 及运行时配置。

实施前复核既有同步入口清单；若发现其他具有 Backend 已提交事实和明确重放规则的 Bot 资料，先更新本文清单和契约测试，再纳入 Backend plan，不留下通用 JSON 镜像或隐式扩展入口。

### 7.3 执行顺序与缺失处理

1. 完成 Backend 鉴权、地址解析，读取修复需要的当前事实并校验。
2. 使用现有 Provider-scoped Bot 查询能力确认目标存在且未删除。不能仅相信 `bcn_registered` 标记或 registry 过期缓存。无法确认存在时不得尝试注册。
3. 若不存在/已删除，返回明确 not-found 错误，所有修复分项均不发送。
4. 先执行一次 `ownership`；必须确认响应属于目标 Bot 且 Owner 与本次事实一致。
5. Owner 确认后执行 `profile`，再重新读取当前协作者事实并执行 `managers`。这让依赖 Owner 身份的资料更新不先被旧 BCS Owner 阻断。
6. Owner 失败、未知或 drift 时停止后续修复；managers 标记 `skipped_dependency`。其他独立分项失败可继续后续独立项；任何步骤发现目标已消失则停止余下步骤。
7. 汇总已执行项与未执行原因，返回本次报告。没有分布式事务，不回滚已经完成的远端写入。

存在性检查不是并发锁：每个 BCS 写入仍必须保持 existing-only。修复不得调用 `register_provider_bot()`、ensure、或缺失时 fallback 注册；BCS 更新中的通用 upsert 必须按 §4.2 排除。

现有响应需作语义检查，不能只判断 HTTP 2xx：

- admin onboard 的 `onboarded=false`：缺失/未成功，不是修复成功。
- ownership 的 missing-Bot no-op / `BOT_MISSING_NOOP`：缺失，不算确认。
- managers 的 missing/no-op：同样不能计为成功，保留接口已有不存在信号。
- 环境跳过、禁用、队列准入：不能计为远端完成。

不增加跨副本锁、版本栅栏或长期修复状态。并发编辑可能使本次快照随后过期，返回成功只表示本次发送得到确认，不承诺永久一致；用户可再次手动修复。

### 7.4 单次执行、生命周期与结果

修复不调用只返回入队结果的 `request_bot()`，也不直接复用默认会重试的 `sync_once()`。复用已有纯 projector 与单次发送 Plugin；必要时抽取有限的一次投递能力，不复制角色映射或实现通用任务框架。

- 每分项最多一次下游写入，沿用经 DI 注入的超时；整体请求有明确截止时间，时间不足的项不再启动。
- 事实读取的 await 同样受剩余预算约束；超时后丢弃线程返回值，不等待它完成后才返回，也不启动后续写入。读取超时发生在 exists 前时返回 504；执行分项读取超时时，该项为 failed/delivery_timeout。
- 不进行内部自动重试，不把失败重新塞回后台队列。已有后台同步完全保留。
- 不跨网络调用持有数据库事务或锁。
- Owner/managers 的生产 Plugin 当前由后台 loop 管理；不能把同一个 AsyncClient 借给 HTTP 请求所在的不同 loop。composition root 为请求侧配置同契约、独立且正确关闭的 client/Plugin，或采用已有安全的同 loop 调用机制；不得新增修复 worker/队列来掩盖生命周期问题。
- 超时/取消后可能已远端提交，结果为 unknown，不能声称未修改或回滚成功。
- 错误信息为稳定、脱敏的有限原因码；不回传原始下游响应、token、完整协作者名单或签名头像 URL。

### 7.5 HTTP 响应约定

复用 Backend `Envelope`；BCS 和 Backend 的 envelope code 体系不同，不能直接透传 BCS code。

**前置拒绝**使用现有 `ErrorEnvelope`，`data: null`：未认证 401、无权限 403（资源隐藏按既有策略）、Backend/BCS Bot 不存在 404、地址歧义或无效事实 409、同步能力未配置/禁用 503。上游存在性检查失败或超时分别映射 502/504，不能伪装 404。

**已经进入修复执行的请求**返回 HTTP 200 的 `Envelope<CollaborationRepairReport>`。HTTP 200 只代表成功取得执行报告，不代表修复全部成功；前端必须依据 `data.status`，不能只按 HTTP 状态弹出“修复成功”。这样保留全局错误 envelope 的 `data: null` 约束，同时提供部分失败明细，不修改所有接口的错误结构。

ownership/profile 执行后，重新读取或投影 managers 的已分类事实错误必须保留此前结果：managers 为 failed，携带既有 RepairFactsErrorCode。禁止将此时的失败转换为 data:null 的前置错误。未分类程序异常及请求取消仍向上传播。

报告的整体状态为 `succeeded | partial_failed | failed`。只有所有适用项确认后才是 `succeeded`；存在 unknown/失败/依赖跳过时不得成功。分项状态为 `succeeded | failed | unknown | skipped_dependency`；字段为 `component`、`status`、nullable `error_code`。unknown 表示结果未确认，不表示远端没有修改。

示例仅为 `data` 部分：

```json
{
  "bot_id": "bot_example",
  "status": "partial_failed",
  "items": [
    {"component": "ownership", "status": "succeeded", "error_code": null},
    {"component": "profile", "status": "succeeded", "error_code": null},
    {"component": "managers", "status": "unknown", "error_code": "delivery_timeout"}
  ]
}
```

整体状态计算：有失败/未知/依赖跳过且至少一个分项确认成功为 `partial_failed`，否则为 `failed`。前置或执行阶段发现缺失都给出稳定的 `collaboration_bot_not_found` 原因，不因 HTTP 200 报告而隐藏缺失。

Backend 持久化读/写失败保留明确错误，不捕获成成功或空成员集合。需要返回报告的分项领域错误必须显式建模；未分类异常仍由现有 500 处理，不用宽泛 catch 把程序错误当成功结果。

## 8. 分层与传播范围

| 边界 | 契约与实现要求 |
| --- | --- |
| Backend HTTP → Service API | 独立 `CollaborationRepairServiceProtocol`（名称可按本地风格调整），请求/结果为 transport-free 类型；adapter 只做授权上下文转换和 envelope 映射 |
| Backend Core → 外部 Plugin API | 复用 Owner/managers Plugin；资料同步通过声明的端口使用既有 transport，不在新 Core 直接导入 HTTP client 或具体 Plugin |
| Backend repository | 复用 Bot、ownership、manager 事实读取，租户/env/稳定地址明确；不新增同步状态写入 |
| BCS HTTP → application | 现有写入请求字段、V1 查询响应；legacy 输出明确隔离 |
| BCS application → core → repo | 展示资料批量读取和头像更新契约；SQL、memory/file 与缓存实现同语义 |
| 文档和测试 | BCS OpenAPI/shared models、Backend 路由授权/admission/错误文档、Context Boundary、Service/Plugin conformance |

新 Backend 模块声明 README Context Boundary，DI 只在 composition root。授权继续通过已有统一机制，不在 repair Core 发明另一套权限判断。public-local 提供 recording/fake 实现，不依赖私有网络。

## 9. 两个独立 plan 的交付边界

本节只定义所有权和依赖；具体任务分别见 [BCS plan](bcs-plan.md) 与 [Backend plan](backend-plan.md)，不可混合为一个执行计划。

### BCS plan（独立执行）

- 只修改 `apps/bcs` 及该模块对应契约/验证文档。
- 负责独立列、两方言迁移、写入扩展及 existing-only 更新保障。
- 负责 V1 参与者投影、批量读取、缓存兼容和 legacy 不变。
- 用模拟 Backend 请求独立验收，不依赖 Backend 同时改完。

### Backend plan（独立执行）

- 只修改 `apps/backend` 及该模块对应契约/验证文档。
- 负责日常链路头像 payload、触发条件与正确清空。
- 负责修复 Service API、端口装配、HTTP/权限、报告、既有投影与单次发送复用。
- 先按本文冻结契约使用 recording/local 实现验证，再连接已完成的 BCS 进行联调。
- 不实现 BCS schema，也不把前端页面实现混入本 plan。

两个 plan 可以独立开发和提交，但 Backend 对新增头像契约及 existing-only 行为有部署依赖。不得在 Backend 中为旧 BCS 发明额外旁路。

## 10. 兼容、发布与风险

1. 先部署 BCS schema 和兼容旧请求的实现，再部署 Backend；旧 BCS 可能忽略新字段或拒绝请求，不能将未升级环境的 2xx 当头像验证证据。
2. 旧行头像 NULL，V1 新字段为增量扩展；前端未消费时不影响旧显示。
3. 混部期间旧写入端不得清空新字段；迁移测试覆盖旧数据、旧请求和重复注册。
4. 回滚先停用/回滚 Backend 新行为，再回滚 BCS 应用；保留 nullable 列和已提交 migration，不删除头像数据、不改 checksum。
5. 单次修复允许部分成功，Owner/managers 变更会影响授权，不能以“UI 信息修复”为由放松权限。
6. 既有异步同步可能与手动修复交错，不增加排序承诺；如果字段仍漂移，用户重新触发，不引入隐藏补偿。
7. 新列表成员摘要增加响应大小及披露面，必须测试既有资源/成员可见性，不只检查头像能显示。
8. 阅读基线中 `bot_service.py` 为 6,355 行、`bcn_service.py` 为 981 行、`bcs-app-session/src/lib.rs` 为 1,068 行。PR #3569 经维护者明确批准，BotService 沿用已有尺寸豁免，仅新增必要调用点；头像投影和修复编排留在独立模块，完整生命周期拆分另行重构。其他新增/修改源码仍遵守 1,000 行限制，不新增尺寸豁免。

## 11. 验收矩阵（实施时执行）

### BCS

- 注册有/无头像，更新替换/清空/省略，拒绝非法类型和显式 null；重连及旧请求不丢头像。
- SQL/memory/file 一致，重启后读取、缓存更新与写失败不污染缓存。
- SQLite、真实 MySQL 的新库完整 migration 链及旧库升级；历史 migration 不变。
- V1 普通 Group/DM/公开 Group list、get，Session list/get 及共用模型返回；legacy/WS 不增加字段。
- 无头像、Human、软删除、缺失 Bot、会话独有成员、重复成员 ID、不同租户/环境和不允许披露的成员。
- 换头像后已有会话显示新值，清空后不因缓存或旧成员快照返回旧值。
- Group/Session 列表名称快照与 registry 当前名称不同或缺失时，分别返回快照或 null；即使头像可查也不补名称。Group 原有 driver/peer 和详情名称补齐不回归。
- 最大页面及高成员数的分批/查询次数断言；新增列表名称不引入读取，唯一新增读取为头像批次；批量读取失败不假装无头像。
- existing-only 资料修复在检查后并发删除时不得 INSERT 或恢复 Bot。

### Backend

- 正常创建/启动已有注册入口携带头像；名称/描述/仅头像/清空头像的更新触发与不相关 ext 更新。
- 修复路由严格匹配确认路径；owner、非 owner、跨租户、App grant、地址歧义和 handoff 后稳定身份。
- BCS 缺失、软删除、读失败、配置禁用分别正确分类；缺失时注册/ensure/create 调用次数为零。
- HTTP 200 + `onboarded=false`、ownership missing-no-op 不算修复成功。
- managers 全量替换、撤销删除、Owner 排除、Space 资格、上限，以及事实读取失败不发送空名单。
- Owner 失败后依赖项不执行；部分失败、结果未知、独立项继续、总截止时间和无自动重试。
- 不生成/增加 caller revision，不修改 Backend 业务事实、凭证或运行时，不重放审批。
- HTTP report/status 与 ErrorEnvelope 的区分；前端消费者只对 `succeeded` 展示修复成功。
- 请求侧 Plugin/client 不跨 event loop 复用，超时/取消/关闭无资源泄漏。
- Service API 单元与 conformance；Plugin 消费者测试通过 `world` 取得真实消费者并断言记录到的调用；架构依赖及协议映射门禁。

### 联调

Backend 注册已有正常生命周期 Bot → BCS 存储头像 → V1 Group/Session list/get 返回 → Backend 改/清空头像 → 再次查询；制造已存在 Bot 的资料/Owner/managers 漂移后手动修复并读回验证。另测 BCS Bot 不存在时明确失败且库内没有新增行。

实施 PR 运行相关模块门禁及适用的统一 singlebox coverage 与 artifact verifier；无法运行的项目逐项记录原因，不以 mock 验证代替真实 MySQL 或跨服务证据。

## 12. 当前文档验证记录

核验对象为本 design 与 [BCS plan](bcs-plan.md)、[Backend plan](backend-plan.md) 的当前修订版，包含基于提交 `187610d26` 的审阅意见修订；不再沿用初版的链接数量。三份文档均未对应实施变更。

已通过文档校验：三份文档的全部本地 Markdown 链接（含 design/plan 双向链接）目标存在、代码围栏配对、JSON 示例可解析、Python 示例可做语法解析、Bash 示例通过 `bash -n`，以及 `git diff --check` 空白检查。计划创建的源码、测试和 validation 文档按各 task 的 Create 声明检查，不冒充已存在或已实现的文件。

已定点复核 Group `project_summary()` 的名称补齐调用、Session 列表投影，以及 CountReads 与其 fixture 的实际所属 crate。没有修改业务代码、数据库或运行时状态，也没有执行任何实施任务。

未运行 Rust/Python 单元、契约、迁移或 E2E 测试，因为本次只有文档修订；第 11 节及两份 plan 中的测试是后续验收要求，不是已通过声明。

### 审阅意见处置（2026-11-02）

| 意见 | 处置 |
| --- | --- |
| P2-1：列表 `name` 来源不明确 | §5.2 明确成员快照/null，§5.3 明确不新增名称读取；BCS Task 6/7 增加快照与查询预算断言。定点读码发现 Group summary 已有 driver/peer 名称补齐，明确保留并在补齐前提取新摘要快照，不采用“列表没有任何名称补齐”的现状判断。 |
| P3-2：CountReads 所属 crate 含糊 | BCS Task 4 写完整源路径及 fixture 三元组，并区分可复用的 wrapper/装配方式与不可照搬的局部测试 schema。 |
| P3-3：计划残留机器临时路径 | 两份 plan §0 删除会话临时审阅文件路径；执行者仍须阅读并遵守仓库 AGENTS.md，不修改仓库规则。 |
| P3-4：验证记录停留初版 | 本节改为三份当前文档的实际核验范围，复核新增双向链接，不保留易失真的初版计数。 |
