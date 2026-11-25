## Context

状态：Draft，2026-11-25。原设计为完整目标；用户后续授权实施，当前仅交付 reader-first 切片，见 [implementation.md](implementation.md)。固定源码基线为 `origin/dev` fetch 后的 `9a9aea2c3b5a0e5a0ed77c85e3790b74dca18bd8`；[research.md](research.md) 区分已检查事实与设计。

约束来源：[架构宪法](../../../../docs/arch/arch.rules.md)、[CI](../../../../docs/arch/ci.enforce.md)、[BCS 分层](../../CLAUDE.md)、[知识标准](../../docs/agents/knowledge-standard.md)。沿用 [friend edge 改革](../../docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md) 的 default-profile 判别和当前 owner/manager 契约，不以本草案重解释既有业务。没有查到直接批准 SystemInvoke 的 accepted ADR；此处均是待评审决策，不宣布系统级 ADR 已接受。

当前 repo port 的注释“ANY active edge”比实现宽：DB/Memory 实际仅接受 `permission_profile | rules`，排除 owner/manager。DB 读取还会把 query/row error 吞为空/部分集合；新安全路径不得复用这种成功假象。现有 admission 不执行 originator policy，且将布尔成功变成 default-profile ref；新增 kind 必须处理这些消费者，而非只通过 Rust exhaustive match 编译。

## Goals / Non-Goals

**Goals**

- 以一等 grant kind 可检索地区分生产系统 Agent 的直接调用权，A→B 与 B→A 独立，授权、申请、审批、撤销可审计。
- 保持好友与 owner/manager 语义和 DB 字符串；可信身份最终落到具体 Bot，操作授权不依赖自报身份。
- 覆盖现有读端、HTTP、双数据库、治理与全链路回归，使实施拆分后可独立验收。

**Non-Goals**

- 原 spec-only 约束已被后续用户的实施/PR 授权替代；当前切片实现类型与读端兼容，不实现管理审批/运行放行，不部署或访问生产。
- 不建立 AgentCode 级全实例授权，不把个人 Bot/Human 扩进系统调用，不授予群消息、成员、管理、发现、好友或任意工具权限。
- 不改变 AgentCard 标准、管理字段、卡片抓取、GroupCard/Fuse、签发 AgentPass、不设计通用 IAM/策略语言。
- 不推出一般 permission_profile/rules 的公共授权生命周期；它们的内部支持不等于已公开。

## Decisions

### D1. 类型、方向与安全域

采用 `GrantKind::SystemInvoke`，serde snake_case / DB **`system_invoke`**。拒绝 `System`（像超级权限）、`AgentCall`（未限定系统用途）、`SystemFriend`（误导社交双向关系）。`invoke` 表示直接调用一个目标 Agent，不是 RPC/HTTP 传输类型。

v1 仅允许 A、B 为不同的、受信 inventory 标记的系统 Bot，同一权威环境和租户安全域；Provider 可不同，但两个绑定必须分别通过受信解析和该域策略。域由 deployment/store namespace 与认证上下文共同确定；当前 `edge_grants` 仅有 env，没有 tenant 列，**共享 env 的多租户 DB 若无法证明 namespace 隔离，不得启用**，后续另行增加持久化域键，不能把 env 当租户替代。

“生产”是经校验配置注入的安全域属性，不在 Core 读取环境变量，也不硬编码字符串 `env == "prod"` 判定真实性。本地 singlebox 使用合成生产域及假 inventory，不接真实生产。普通测试/预发域默认不授予该 kind；它们可保留并查询审计历史，但不得产生有效调用授权。系统资格不是 owner/manager、`agent_code` 存在、Card name、用户可编辑 `catalog_labels`、来源 IP 或请求中的 `system=true`。资格提供者须经明确契约、composition root 注入，既有可信注册资料可复用，不再自造可变布尔标签。

### D2. 存储形状与旧类型兼容

| kind / DB 字符串 | ref | rules | Runtime candidate | Friend / authority |
| --- | --- | --- | --- | --- |
| `PermissionProfile` / `permission_profile` | 目标 profile ID | NULL | 保持原样 | 仅目标 active default profile 是 friend |
| `Rules` / `rules` | 既有 opaque ref | 原 inline JSON | 保持原样 | 不是 friend / authority |
| `Owner` / `owner` | 0 | NULL | 否 | Human→Bot 管理元数据，不赋调用权 |
| `Manager` / `manager` | 0 | NULL | 否 | Human→Bot 管理元数据，不赋调用权 |
| `SystemInvoke` / `system_invoke` | **0** | **NULL** | 是，但不是最终 allow | 不是 friend / authority |

新增类型统一 `status=approved|revoked`、`originator_policy_type=same_as_from`、`originator_policy_data=NULL`，不新增 suspended/pending edge 状态；pending 只属于 request。旧字符串、profile revision/digest、friend `Any`、owner/manager shape、authority precedence、默认 profile seed 均不变。新类型不要求、引用或自动创建 default profile。

复用现有唯一键 `(from_id,to_id,env,grant_kind,grant_ref_id)`，因此同一方向至多一条 system edge，同一 Bot 对可与 profile/rules 边共存，整表继续容纳有效的 Human→Bot 管理边；不表示同一对 Bot 可以拥有 Owner/Manager 类型。重授予复用同一 edge_id、恢复 approved，但**必须**经过新一次授权与审计，不允许客户端重放旧 idempotency key 越过 revoke 复活。新边不是 profile 的新名字，禁止用 profile ID / `rules.ref` 模拟该业务类型。

数据库的 VARCHAR(32) 来自 MySQL migration 036；17 字符的旧 `permission_profile` 本身不能根据历史 014 的 VARCHAR(16) 判断可用性。`system_invoke` 长 13，无需为它扩大此列。当前无需 enum DDL，但索引、请求关联的 version/fence、审计和幂等元数据仍需未来 additive migrations；不得修改 014/036 或其他已提交 migration/校验和。

### D3. repo 候选查询与真正的运行决策分开

- `list_active_grants(from,to,env)` 扩展候选集为已批准的 `PermissionProfile | Rules | SystemInvoke`，始终排除 owner/manager；不执行 caller、originator 或 operation 决策。
- `is_authorized(from,to,env)` 扩为同一候选集的存在性投影，签名保持兼容；其 bool **不是**系统调用可执行证明，不能据此直接下发操作。
- `has_friend_edge`、`list_friends(_paginated)`、`find_human_friend_bots`、friend request/通知/writeback/精确撤销均维持 default-profile-only；同一对有新边也不得变好友。
- 新安全路径增加 fallible repo 读取契约（拟名 `list_runtime_grants_checked`）返回 `ServiceResult<Vec<EdgeGrant>>`，DB query、JSON、未知 kind、损坏行均失败，不能丢弃错误行，也不能在 error 后 public_default fallback。既有 vec/bool 接口保留兼容，不强制本次为所有旧路径改变错误语义。
- 通用 `check_admission` 及既有 `build_authz_context` 继续采用旧 kinds 的准入语义：迁移消费者到显式 kind 过滤，不能因扩大的 repo bool 就合成 default-profile grant，也不能把未验证的 system grant 放进旧上下文。friend revoke 已按 kind/ref 过滤，仍须回归。
- 新 application 用例（拟名 `SystemInvocationService::authorize_invocation`）接收已解析 principal、env/domain、A、B、真实 originator、operation，不接 HTTP types。对 `system_agent.invoke` 操作通过统一 admission/authz 扩展点调用，不在每个 router 复制策略。
- 现有 HTTP `GET /bots/{id}/admission` 不新增语义或放宽：**仅有 SystemInvoke 时它不会因此成功**。已有其他授权/public/collaboration 路径独立照旧，不得在响应中谎称命中了 SystemInvoke。

系统调用决策顺序：认证及 A 绑定 → 域与双方系统资格/存活/非 hidden → operation 是直接 Agent 调用 → `originator == A` → fallible 精确候选查询 → kind/ref/policy/status 完整性 → 当前批准代次匹配 → allow。任一步失败都不能由这条新边放行；不允许将其降级转到 public_default 或默认 profile。无 system edge 时既有调用策略可独立决策，但不能冒充本能力的成功；测试使用 protected 目标排除独立授权来源。

A→B、B→C 不意味着 A→C；B 执行由 A 发起的委托链时，其 originator 仍为 A，不能借 B→C 的 same_as_from 冒充 B。若 B 自身启动独立任务、可信 originator 确为 B，则按 B→C 重新决策。既有 parent lineage 不可由 body 伪造、清空。

`AuthzGrantRef` 的 system 投影使用 `kind=system_invoke, ref_id=edge_id, source=edge_grant, revision=批准代次, digest=None`；此处 ref 的 kind-specific 意义是**边记录 ID**，不是存储哨兵 `grant_ref_id=0`、不是 profile ID。批准代次从关联 version 元数据取得，每次 revoke/regrant 前进，供缓存与审计 fencing；旧 kind 的 ref/revision/digest 不改变。读者不支持新 ref 时不投递；在不可变的已准入业务操作内沿用既有完成语义，但后续调用必须重新查权，不承诺强制中断已开始工作。

### D4. AgentCard、bot_uuid、agent_code 与可信解析

`EdgeGrant.from_id=A.bot_uuid`、`to_id=B.bot_uuid`；这不是对某 AgentCode 的所有实例授权。`PermissionProfile.bot_id` 仍指具体 Bot。精确字符串比较，不 trim/case-fold；沿用身份 NO PAD collation 及对应 SQLite 语义。

管理入口 v1 要求显式 `from_bot_id` / `to_bot_id`，可读审计展示 agent code，但不提供“输入 code 自动选第一个”的便捷写接口。运行入口从认证链获得 A，body 中 A 只可做一致性断言。若宿主现有协议仅给 AgentCode/AgentPass，须先校验 issuer/signature/audience/时效/撤销及 Provider/actor 绑定，再在 env/tenant/Provider 范围内解析为唯一 bot_uuid；多个、零个、空值或查询失败均 fail-closed，原 `find_bot_by_agent_code` first-match 不能直接作为授权 resolver。

AgentCard 提供发现/能力描述，不证明谁在调用，也不自动授予调用权；Card 同名、URL 相同、刷新、删除/隐藏投影或重注册不得把旧边迁到新 Bot。持久身份绑定改变、退役或失去系统资格后禁止使用旧批准代次；重新激活/归类/绑定后需要新审批，不可仅因旧 edge 仍为 approved 而复活。实现须有持久身份/资格代次或原子撤销保障；若资格源不提供可靠代次及失效契约，则阻止启用，不以只读“当前为 true”替代。

并行 AgentCard 身份 Draft 当前使用外部 `agent_id` ↔ DB `agent_code`，允许多个 Bot 共享且可缺失；它不是本基线已合入的实现，也不是身份凭证。本规范不要求因创建 system grant 而回填/强制变更 Card 字段；可凭已有受信 bot_uuid 完成认证和资格校验时，不额外要求非空 AgentCode。若特定宿主认证只能依赖 code，缺失就明确拒绝该认证链，而非统一虚构 ID。

### D5. 管理 Service API 与审批状态机

新增显式 application `SystemInvokeGrantManagementService`，core 策略只依赖声明的 repo/资格事实；delivery 仅调用 application，store 独占 SQL。下列名字是拟议契约，不是现有 trait：`grant`, `request`, `decide`, `cancel_request`, `revoke`, `get_grant`, `list_grants`, `list_requests`, `get_degree`。repo 增加专门事务命令，不能由 application 串接现有 insert/decide/backfill 冒充原子提交。

权限矩阵（均先有有效认证、正确域、显式目标范围；不启用旧“无身份放行/legacy auto-claim”）：

| 动作 | source owner/manager | target owner | target manager（仅现有角色） | 限定域/目标的治理 service |
| --- | --- | --- | --- | --- |
| 申请 A→B | 允许 | 仅也有 source 权限时 | 仅也有 source 权限时 | 有 request 委托时 |
| 直接授予/批准/拒绝 | 不因 source 角色允许 | 允许 | **不自动允许** | 有 approve/grant 委托时 |
| 取消 pending | 原申请者且仍有 source 权限，或 source owner | 走 reject 而非冒充取消 | 不因 target manager 允许 | 有 cancel 委托时 |
| 撤销已批准边 | 允许止损 | 允许 | 不自动允许 | 有 revoke 委托时 |
| 读本方边/请求/出入度 | 本方受控 Bot 范围 | 本方受控 Bot 范围 | 本方受控 Bot 范围 | 明确 query 范围 |

治理 service 的显式 scope 是新增管理能力的凭据委托，不等于赋予 edge endpoints owner/manager，不允许普通 Bot 自助批准。双向共用管理员不要求额外虚构双人审批；若需 four-eyes，另行策略评审。

`RequestKind::SystemInvoke` / `system_invoke` 新增，复用 `PermissionRequest` pending/approved/rejected/cancelled、request_id 外部 UUID、edge_id 批准后回填；`requested_ref_id=Some(0)`、`requested_rules=None`。理由是业务类型必须可区别，不能假装 Connect/Rules。新请求的审批绑定资格/身份代次、scope、申请人和审计动作；额外元数据使用 typed 关联存储，不藏进 `message`/rules/decision_reason。friend inbox 必须过滤 `request_kind=connect`，新类型不能进入 friend accept/reject/cancel 或连带批准反向 pending。

状态：pending→approved/rejected/cancelled；终态只能读不能改。approved 同事务创建/恢复唯一方向边并回填 edge_id；请求失败、审计失败、CAS 竞争失败整体回滚。source 管理者或身份变化使旧审批依据无效时返回 409，需要重新申请；审批时重新检查 source 授权及双方资格，不能只信旧快照。直接授予无须伪造 pending request，但必须写同等批准审计。

创建/审批/撤销对同一 edge 线性化，客户端使用 `Idempotency-Key`，批准/撤销还带 `expected_version`；重复相同 key+payload 返回原操作结果，key 改 payload 返回 409。相同审批终态重放返回原结果，冲突终态/旧 version 返回 409。撤销后重放旧成功创建返回历史回执（附当前 revoked/version），**不重新写 approved**。新的重授予需要新 key 与当前 version。撤销已 revoked 边在权限仍有效、version 兼容时幂等；不存在或无权按防枚举规则处理。

审批/revoke 事务必须同时更新边、request、approval version、审计及幂等回执，锁 Bot/edge/request 的顺序稳定，以 env+byte-order Bot IDs 排序，权限/资格版本受 fencing。治理配额检查在同一串行化临界区；不能先 count 再无锁 insert。外部资格网络读取不能持有 DB 锁；使用可验证版本快照+CAS，不能证明新鲜则拒绝/重试前重新解析。失败和结果不确定分别返回明确错误；timeout 不盲目重试写，客户端用同 key 查询/重试恢复回执。

### D6. HTTP contract（全部拟议、尚未注册）

采用独立 BCS 管理前缀 `R=/api/v1/collaboration/system-invoke`。未来 OpenAPI 若暴露，镜像 `R=/openapi/v1/collaboration/system-invoke`，复用同一 application，不独立实现策略。宿主 RPC 另列 delivery contract 并过 conformance，不把内置 repo port 叫公开接口。

| Method + R 下路径 | 语义 / 响应 |
| --- | --- |
| `POST /grants` | target 授权方直接批准；201，返回 grant + version + audit/action ID |
| `POST /requests` | source 提交申请；201 pending，edge_id=null |
| `GET /requests` | 本方 received/sent，kind 固定 system_invoke，分页、status 过滤 |
| `GET /requests/{request_id}` | 获授权参与方读申请及终态 |
| `POST /requests/{request_id}/approve` | 原子批准；200 request + grant |
| `POST /requests/{request_id}/reject` | 200 rejected，不产生边 |
| `POST /requests/{request_id}/cancel` | 200 cancelled，不产生边 |
| `GET /grants/{edge_id}` | 授权范围内读精确 system edge，含当前 version |
| `POST /grants/{edge_id}/revoke` | 200 revoked，body 有 expected_version / reason；不以 pair 删除 |
| `GET /grants` | 指定 bot_id + direction=inbound/outbound + status，limit/cursor |
| `GET /degrees` | 指定单一 bot_id，返回 active/pending 出入度与 snapshot_at |

创建/申请体为 `{from_bot_id,to_bot_id,reason}`；创建 additionally `expected_version`（新边为 0）。approve/reject/cancel 的 body 为 `{expected_version,reason}`，其中 version 指申请行版本；revoke 的 version 指边批准/撤销版本，不能互换。kind 固定由资源表达，不接受客户端选任意 kind/ref/status/rules/policy/env/租户/created_by/decided_by。授权域从认证和路由部署范围推导；跨域 ID 返回 scoped not-found，不跨域查询猜测。身份或 enum 字段 alias 必须明确，不接受未列出的字段。

响应最小字段：grant `{edge_id,from_bot_id,to_bot_id,grant_kind,status,version,created_at,updated_at}`，request `{request_id,request_kind,status,edge_id,from_bot_id,to_bot_id,version,created_at,updated_at}`，decision/action audit ID；不返回 token、原始身份凭据、全量 Card 或规则内容。重放成功按原 receipt 的 HTTP status，附 replay 标记与当前状态，避免把历史 approved 误读为当前有效。

错误：401 缺失/无效认证；403 已识别主体但无操作权；404 不存在/不可见域或资源；400 类型/shape/非法 self/Human/未知字段；409 状态/版本/资格快照/幂等冲突；429 明确治理限额；503 身份/资格/DB 不可用或提交结果不确定（不同稳定 code）。任何 4xx/5xx 不得返回成功空页或部分写入。沿用仓库错误信封、request_id/trace 语义；最终 code 清单与 OpenAPI 一起冻结，禁止暴露 DB/身份库细节。

旧 friend-connections、friends、friendships、admission、AgentCard API 不加“kind=system”快捷参数；无通用“枚举增加即公开创建”机制。默认开关关闭时新写入口不可用、旧路径保持原样；只在专用受认证 router 挂载。

### D7. 治理查询、索引与成本

治理只计本 kind，所有条件含安全 namespace、精确 env，active=approved；`out_degree=COUNT(DISTINCT to_id)`、`in_degree=COUNT(DISTINCT from_id)`，自环禁止且 ref=0 唯一，正常时等于 active 边行数。pending 入/出申请单独计数，不与 granted degree 混加；所有计数从 store aggregate 得出，不全量拉边后客户端数。revoked/history 可查询但不算 approved 度数。治理接口的 active 明确指**存储 approved 状态**，不是当前实际可调用证明；资格失效但尚未 durable revoke 的行仍保守计入配额，不能因资格查询失败而少计或伪装有效授权。失效动作持久撤销后度数才减少；实际调用仍立即按资格/身份 generation 拒绝。响应注明 `count_basis=stored_approved`，不为计数逐边调用外部 inventory，不声称统计了有效调用权限。

分页 keyset 按 `edge_id`（请求按内部稳定排序键，外部仍 request_id），默认 50、最大 100；cursor 绑定 bot/direction/domain/status/kind，条件不匹配拒绝。一次授权查询不支持不受限的双向 OR 或全平台榜单；按 bot 做有界出/入查询。degree 在一个快照返回两向计数与 snapshot_at；跨请求分页不承诺冻结整个关系图。全平台扫描仅独立运营离线任务，非请求热路径。

现有 from/env/status、to/env/status 和 authority 的 env/to/kind/status 索引是否足够须 EXPLAIN。拟追加（不是当前 DDL）：

- 出边 `(env,from_id,grant_kind,status,id)`；
- 入边 `(env,to_id,grant_kind,status,id)`（评估是否扩展已有 authority 索引，避免重复）；
- 请求两向 `(env,from_id|to_id,request_kind,status,id)`；
- pending 活跃申请去重由专门唯一约束或事务锁保证，不能只靠 find_request；审计按 domain/pair/action 时间分页。

部署前在 MySQL/实际宿主兼容 DB 和 SQLite 验证完整 migration、复合键字节长度、身份 collation、rows examined 与计划；不能因 SQLite 过测宣称 OceanBase 合规。首版不引入正向权限缓存；如部署已有缓存，所有相关 writer 必须处理 revoke/代次/域隔离，失效完成前不确认新授权决策成功。不得以 TTL 为撤销唯一保证。

正常调用预算：1 次精确 fallible edge 读（唯一 system kind 最多 1 行）+ 有界、批量的双方身份/资格与批准代次读；具体额外 round-trip 常量在实现前记录。列表 1 次 `LIMIT <=101` 边查询（多 1 判断 next），最小资格/展示投影按 <=100 批量读取，不逐边 N+1；degree 1 个快照内最多 2 个 aggregate queries，pending 另计最多 2 个。治理查询不承诺每次 count 扫固定行数，须以大出/入度数据及 EXPLAIN 验证；大图改离线审计不能放任无界热查询。

限额 `max_out_degree/max_in_degree/max_pending_*` 由 validated 配置显式给出正数，无“未配置=无限”；spec 不凭空指定生产容量。实施前安全/运维确认值，启动缺失即拒绝启用写端；检查与新增边提交原子，重复批准不占两份。超限拒绝新批准，不隐式删除旧关系，不限制有权撤销的止损能力。

### D8. 分层责任与传播

| 层 / 契约方向 | 未来责任 | 禁止 |
| --- | --- | --- |
| `bcs-domain` | kind/request 枚举、纯 shape/invariant；无凭据/SQL | 引入 HTTP/DB/宿主 SDK |
| Service API / inbound | application 管理与运行鉴权 use cases，core 纯策略契约 | 将 router 当唯一 API 原型 |
| repo ports / outbound business | fallible 候选、事务命令、分页/聚合/审计 | 把 SQL 或 DB 事务对象泄到 core |
| Plugin API / outbound infra | 身份/资格事实契约与 DbPlugin 能力评估 | 为凑层级在 core 直接调 DB plugin |
| `services/*-store` | SQL、mapping、cache、事务/锁/唯一约束 | 让 HTTP/宿主直接读写 edge 表 |
| HTTP / SDK | parse auth、映射 DTO/errors、调用 application | 复制 approval/资格策略 |
| composition roots | 选择 real/fake inventory、DB、统一 authz hook、验证开关 | 散落读取 env、绕过标准 hook |

资格属真实可替换边界，契约、local fake 与 host provider 分开，避免仅为 enum 建全新框架。新增跨领域授权必须经集中 authz extension point（架构规则 12），由 application orchestration 复用，不让每个 Core 依赖宿主权限服务。更新相关 CONTEXT.md、契约清单与 conformance driver 映射；Python 专用架构测试形状不能冒充 Rust conformance。

## Risks / Trade-offs

- [枚举新增使旧 reader/SDK decode 失败] → reader-first rollout、显式 capability gate，拒绝未知值，不映射 profile/rules/owner。
- [bool 扩展产生 default-profile 漏权] → 先隔离旧 admission/AuthzContext 消费者，再开 writer，测试新边单独存在。
- [rules 看似细粒度但实际未执行] → v1 NULL-only，拒绝任意 JSON；未来需独立规则 schema/version/digest、action/context 输入、执行器/deny 优先级与全链路证据。v1 是 Agent 边界的粗粒度调用权，无法约束 prompt 或目标 Agent 内部自行执行的工具，目标自身工具/业务权限仍须独立校验。若只需现有 target profile，另用旧 kind，不制造 SystemInvoke+profile 双重语义。
- [撤销旧调用关系不代表撤销所有访问] → 告知独立 profile/friend/public 路径可能继续有效；只撤销本 kind，不误删其他授权。
- [资格失效后旧批准复活] → 身份/资格代次 fencing，durable 失效；缺乏版本证明阻止 rollout。
- [幂等 upsert 与 revoke 竞争] → 事务、expected_version、独立 action receipt，旧 key 不复活；用真实 DB 可控 barrier 回归。
- [生产语义依赖内部 inventory] → 公共代码只契约和安全 fake；未交付 host provider 时默认 fail-closed，不把 docs 当线上证明。

## Migration Plan

1. 独立评审本规范、安全角色矩阵、租户隔离、资格代次契约、配额与 HTTP contract；本轮不进入 apply。
2. 增量实现 reader/mapping、fallible repo/原子命令、迁移与审计，关闭写端；保留既有历史行和字符串，不批量把 Rules/Profile 转成新 kind。
3. 先更新所有运行 consumer、DTO/SDK、AuthzContext 接收者、friend request kind 过滤及集中 hook；用 capability inventory 证明旧 reader 已退场。
4. 全部目标 DB 执行新 migration 后，以 synthetic local/测试授权数据验收；升级只读版本不会凭空创建授权。
5. 宿主完成可信身份/资格 provider、域隔离、limits、审计与代次失效接入，明确测试环境与允许调用的 fake target，再灰度启用专用写端。没有部署授权不操作生产。
6. 回滚先停新写/新 system invocation admission，保留审计和新增 schema；撤销/失效剩余 system grants，仍需兼容 reader 处理历史/未知行。**仅把 status 改 revoked 不足以让旧全量 reader 安全回滚**：确认所有旧访问路径过滤新 kind，否则保留 reader 升级或先隔离新类型数据。禁止把 system 边改写成旧 kind 来回滚。

## Open Questions

以下是实施/启用门禁，不是已实现能力；没有答案也不将本次 spec 扩成实现：

1. 谁负责生产系统 Agent inventory？它是否提供稳定分类/绑定 generation 及可验证新鲜度？现有公共 Bot/Card 字段不能替代这项证明。
2. 目标部署的租户是否已由物理 DB/namespace 完整隔离？若不是，先补域键迁移及唯一约束，再启用。
3. 生产 limits 数值、审计留存和 host compatible DB 的验收负责人/环境待批准；不得随意默认为无限或宣称 MySQL 测试等同 OceanBase。
4. 是否确需 OpenAPI/宿主 RPC 同时暴露管理入口？本草案定义 HTTP 目标契约；最终启用面须由 owner 审核，不能假定所有传输自动发布。
5. 若业务未来需要 Human originator 委托、多跳或工具级 scopes，应另提 capability；本版 SameAsFrom 与 rules=NULL 不通过放宽 validator 偷换。
