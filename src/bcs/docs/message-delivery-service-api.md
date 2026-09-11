# BCS 消息队列 Service API v1

本文描述单实例消息拥塞控制的后端契约，不包含 Workbench 前端实现。
详细设计见 [设计方案](plans/2026-09-04-bcs-message-congestion-control-design.md)。

## Run 级回复归一化与内部汇总

队列 Group 回复不再假设引擎 final 包含全文。按 env/session/发送 Bot/run 隔离，
读取已经持久化的 `chat` 段 H，加上当前内存段 C，对 final 文本 F 逐次判断：

| 条件（按顺序） | 完整正文 | 最后展示段 |
| --- | --- | --- |
| F 为空 | H + C | C |
| 已累计全文非空，F 以其开头（含相等） | F | F 去掉已结束段前缀 |
| C 非空，F 以 C 开头（含相等） | H + F | F |
| 其他 | H + C + F | C + F |

已结束段之间用一个换行组装；原始 delta 直接连接，不添加换行，不做任意子串或模糊去重。
这是启发式兼容，不保证识别改写型快照；歧义时优先保留内容。`好的` 后再次 final `好的`
会保留一份。工具参数、结果和 thinking 不进入正文。新 `run_reply.content.normalization`
保存版本 1、判断方式、原始 final 与已结束段消息 ID，仅内部排查使用，不输出到日志或消息事件。

需要队列处理的成功 Group 回复持久化一条内部 `message_type=run_reply`，`content.text`
保存归一化全文。每个目标 Bot 一条 delivery，共享这条消息；`source_message_id` 不引用
末条历史记录。末段非空时另写普通 `chat`，同时创建其既有 `message.created` 事件。
展示段、汇总消息、目标准入、原输入 delivery 终态在同一事务提交，CAS/写入失败全部回滚。
幂等键受 env/session/sender 约束，run 作为键的一部分，重复 final 不重复入队。
全 run 无正文只结束输入运行，不生成空白下行；error/abort 保持既有部分正文保存策略，
不会当成功回复广播。普通非受管 Group 仍只写展示历史；task/A2A 专有回复语义不在此轮改造。

history 的两种分页查询均在 LIMIT 前排除 `run_reply`，内部按 ID 读取保留原始正文。
汇总不发 `message.created`，因此不会进入会话预览或实时/IM 消息广播。会话序号允许间隙。
旧 `queue_display_text` 投影保持兼容，不做历史回填。24 条/128 KB 仅限制 Inject 发送组装，
不截断持久化完整正文。

H 的仓储读取按 env/session/sender/run 精确过滤、session_seq 排序，兼容旧 JSON 字符串
及对象正文，排除工具和汇总；SQLite 025/MySQL 024 增加对应索引。恢复可重建已提交文本段，
不能恢复崩溃前尚未落库且上游不重放的 delta。旧二进制不认识内部汇总过滤，回滚须保留
history 过滤兼容补丁，不能直接降级后把内部诊断正文暴露给客户端。

Worker 内部仓储 `work_batch(Control)` 不再使用全局 delivery ID 游标。
发送超时、运行超时、待发送 abort、abort 超时分别按索引范围查询；每轮 32 条时
各预留 8 条，空余容量借给其他类，合计不超过 32 条且按 delivery ID 去重。
各超时类按 deadline、delivery ID 排序，待 abort 按 delivery ID 排序。
成功处理会退出该类待办集，因此从最早待办重读；Recovery 仍使用原 ID 游标。
SQLite 024 / MySQL 023 增加待 abort 索引，远端启用前必须应用迁移并验证执行计划。
此变更不改变外部 HTTP/WS 协议或消息状态机。

## DB 策略与动态管理

### Inject 单次发送预算

环境级策略新增 `max_context_messages`（默认 24，范围 1～1024）和 `max_context_bytes`
（默认 131072，即 128 KiB，范围 512～16777216）。不增加 Bot 覆盖，旧 DB JSON/请求缺字段
按默认兼容；显式 0、过小、越界值返回策略非法。完整替换时建议先 GET 再修改并 PUT。

只限制随 Send 附加的 Inject 历史，UTF-8 bytes 包含历史标题、发送者/序号包装、分隔符和
省略提示；当前 Send 正文不截断。它不是 token 限制，不涵盖引擎会话历史、系统提示词或
多模态成本，也不保证整个请求一定适合任何模型窗口。

从最新绑定历史向前选择，最多条数且不超过预算；遇到不适合的下一条停止，不跳选更早小消息。
最新单条过大则安全保留 UTF-8 尾部并标注前部省略。选中项最终正序发送，舍弃较早历史时
附省略提示。只附加选中历史允许的附件（Inject File 规则不变），截尾单条保留其允许附件；
整条舍弃的附件不单独发送。原 `bcs_messages` 正文/附件不修改或删除。

新增 SQLite 023 / MySQL 022 迁移及 delivery 字段 `context_selection_json`，持久化选择版本、
条数/bytes 策略快照、总绑定数、选中 delivery ID/版本、UTF-8 起点、实际历史 bytes，不复制
正文或附件。send-start 原子固定选择，安全重试保持不变；unknown 保留绑定、不重发。
首次 send-start 前取消 carrier 仍释放所有绑定并允许后续重绑；首次 send-start 后只能取消
整个 carrier，不能单独撤回 bound Inject（包括明确未发出后的安全重试等待期间）。

carrier 终态时真正附加的 Inject 为 `consumed`；预算舍弃的为 `discarded_context`，内部原因
`last_error_code=context_limit`，保留 carrier 引用且不再参与调度/drain。旧 active 记录没有
selection 时按升级前语义消费全部绑定。状态 API/事件可能新增该终态值，消费者需兼容。

生产准备最多读取条数上限+1条 metadata，逐条获取正文到预算为止；不全量加载绑定正文。
单条原始 JSON 仍可能很大，不宣称硬内存 cap；关联 metadata 的准入/终态事务仍可能随积压增长。

部署先应用新增迁移再启动新二进制，不能只部署代码而缺列。新配置缺字段默认生效，但不改
已有 flow/Bot 开关。回滚需要使用能识别 `discarded_context` 的兼容二进制；旧二进制可能无法
解析新终态，不能将舍弃记录改回 pending 来“修复”回滚，否则会重放历史。不要删除原始消息。

验证：新增上下文 7 个测试覆盖 0/24/25 条、ASCII/中文/emoji、精确包装预算、超大最新消息、
不跳过中间超限消息、稳定安全重试、unknown/recovery、终态消费与舍弃、取消重绑及附件筛选。
Memory/SQLite 共享仓储契约覆盖选择 JSON 往返；1 万条 bound Inject 的 SQLite 查询测试验证
只返回 25 条 metadata、完整 count、命中有序索引且不临时排序。配置缺字段/非法值和独立日志
新增编码也有测试。未重启真实服务，未运行真实 MySQL/OceanBase、真实模型 token/附件成本验证。

队列业务策略以环境为作用域，保存在 `bcs_message_delivery_policy`；一行保存完整 JSON、
版本、修改人和时间。类型开关、默认策略、Bot 局部覆盖、TTL、安全重试及 dispatch pause
均由管理接口修改。启动读取 DB；不存在记录时返回版本 0、全部关闭，不自动开启。

本地不再需要任何队列配置，也不需要为此接口配置 ServiceKey 或管理 scope。
旧 `message_delivery.lock_path` 已删除；配置采用 deny_unknown_fields，残留该字段会报错，
升级前应删除，而不是依赖它提供互斥保护。

- `GET /admin/message-delivery/policy`：查询当前已生效完整记录。
- `PUT /admin/message-delivery/policy`：完整替换业务策略；使用 GET 的版本。
- 旧路径 `/openapi/v1/admin/message-delivery/policy` 已移除，不保留 alias。
- 401：未认证或凭证无效；403：认证为非 Human；409：版本冲突；
  400：策略非法或当前不具备调度能力；503：持久化/服务不可用。

这两个接口允许**任意通过既有认证入口认证的 Human 管理整个环境策略**。
不要求 owner、群成员或 admin role；`/admin` 路径本身不代表额外角色校验。
HTTP 与应用层均检查 Human，`updated_by` 使用可信 `human_<user_id/staff_no>`，
不接受请求体自报 actor、任意身份 header 或机器凭证作为 Human。

身份来源和本地边界：

- 生产通过已配置的认证插件链验证 Human Cookie/OAuth 会话，例如校验签名、有效期和身份存储的
  `bcs_session` 会话；仅启用 Bot session 认证的部署不会因此自动拥有 Human 登录能力。
- 入口沿用服务端认证配置，不从请求中的群 ID、Bot owner 或客户端 actor 字段推断 Human。
  Bot、Provider admin、ServiceKey 请求不能借助同请求 Cookie 或本地 mock 降级成人类。
- LocalAuthPlugin 可根据配置的 mock_user_id 生成本地 Human；若显式启用 allow_mock_headers
  （或由 bootstrap 的 BCS_AUTH_MOCK=1 开启），认证插件可读取 X-Mock-User-Id。
  接口本身不新增对 mock header 的信任。禁用该选项时伪造 header 不会覆盖身份。
- 配置了固定 mock_user_id 的本地进程，无 Cookie 的请求也会被本地认证插件识别为该 Human，
  因而可以管理策略；这是开发模拟，不是生产认证。不要向不可信网络暴露这种配置。
- 显式机器凭证优先识别：有效 Bot/Provider/ServiceKey 返回 403，无效凭证返回 401；
  Bearer/显式 Bot token 不会回落到 Local mock。旧 API key 的其他业务权限不变。

PUT 结构化审计记录可信操作者、尝试/完成时间、预期和前后版本、变更字段名以及成功/失败；
认证失败时身份/版本记为未知，不读取私有策略用于补日志。不记录 Cookie、token 或完整 payload。
请求体格式错误也记录拒绝结果。DB 只保留最新修改人/时间/版本，本轮不新增审计流水表。

PUT 示例（数值是开发初始值，生产需压测）：

```json
{
  "expected_version": 0,
  "policy": {
    "flow_enabled": {
      "group": true,
      "direct_a2a": false,
      "task": false,
      "system": false,
      "state_machine": false
    },
    "defaults": {
      "mode": "enforce",
      "max_running": 1,
      "min_send_interval_ms": 1000,
      "max_queued": 20
    },
    "bots": {
      "exception-bot": {"max_running": 3},
      "excluded-bot": {"mode": "off"}
    },
    "queue_ttl_ms": 300000,
    "safe_retry": {"max_retries": 2, "backoff_ms": 1000},
    "max_context_messages": 24,
    "max_context_bytes": 131072,
    "pause_dispatch": false
  }
}
```

Bot 未覆盖的字段继承 defaults，新注册 Bot 无需新增配置记录。
首次读取的 defaults 是 off / 1 / 1000 ms / 20；开启前应显式核对这些数值。
PUT 是完整替换，不是 JSON merge patch；删掉 Bot 条目即恢复继承。
TTL / safe_retry 可为 null，分别表示不自动过期、不自动安全重试；单个 Bot 的可选覆盖
字段为 null 或省略均表示继承。更新成功返回递增 version、policy、updated_by、updated_at_ms。
数据库 CAS 失败不发布快照；提交后在同一写锁内发布，后续调度使用新版本。
更新任务有界，接受后独立于 HTTP 请求生命周期完成提交和发布；连接断开不取消已接受的
更新。响应丢失时先 GET 核对版本，不能假设写入失败后直接覆盖重试。
不支持运维绕过 API 直接改 SQL：查询发现 DB 与进程版本不一致会报错，需要重启恢复一致性。

### 生效与 drain

- 降低 max_running 不 abort 现有请求，等 active 降至新限额后再发送。
- 降低 max_queued 不清理旧队列，只限制新准入。
- 发送间隔基于保留的上次 send-start 单调时间重算，不重置历史。
- TTL 只用于新准入的 expire_at，不追溯改写已有记录。
- pause_dispatch 仅暂停新 send；状态、终态、取消、过期处理继续。
- 关闭类型或 Bot 后，已准入消息及其回复继续受管 drain；存在未完成工作时
  对应 Bot 的新请求返回 queue_draining，不能转到 legacy 越过旧队列。
  未消费 context 需要显式取消/消费，Unknown 必须取得可信结束证据。
- 开启前仍应确认原 legacy 运行已结束；新策略不接管旧 ChatRun、不补发历史消息。
  未接入类型与受管类型之间不承诺共同限流或有序。

生产部署需要 MySQL/OceanBase 019、020 迁移；本地自动运行 SQLite 020、021。
必须先部署 schema；DB 读取或恢复失败阻止启动。Memory 不允许生产 enforce。
持久化模式即使策略全部关闭也运行空闲调度器，随后 Human GET/PUT 可动态开启，无需锁配置或再次重启。
工作任务失败或退出后新准入关闭、动态开启也拒绝；生命周期保护不依赖文件锁。
同一 DB 只能有一个 BCS 调度实例；同机和跨机都不再自动检测并发实例。
部署系统必须保证先停止旧实例并确认退出，再启动新实例，发布期间也不能重叠。

旧 TOML 中非默认的 flow_enabled、bots、TTL、safe_retry、pause_dispatch 会导致启动拒绝，
不能覆盖或隐式导入 DB。迁移步骤：停止旧入口并排空/确认旧运行；备份旧策略；
删除文件里的业务策略与旧 lock_path；部署 schema 并启动；通过管理 API 写入策略。
存在待恢复 delivery 时，即使 DB 策略关闭也执行恢复，恢复不会按当前开关过滤。
不引入外部 MQ、outbox 或分布式 lease。

首批就绪类型为 group（自由聊天群及主从群），包括 WebSocket、HTTP 群聊/会话发送、
持久群普通 Bot 消息、IM 入站及其回复路由。system callback、task、Direct A2A、
state-machine 保留原路径；误开启未就绪类型返回 queue_flow_not_ready。

`provider_http.queue_persistable_headers`（默认空）是 `bypass_headers` 的子集，
用于显式批准可明文持久化的非敏感路由 Header。名称大小写无关；最多 16 个、单值
1024 字节、名称和值总计 8192 字节，禁止重复名称、控制字符和已知凭证类名称。
自定义字段是否含敏感信息仍需部署者评审；Cookie/Authorization 不支持持久化。
不再因透传白名单非空全局禁止 enforce。实际 HTTP Provider 目标携带未批准 Header 时，
在准入事务中仅将该目标记为 `failed`，响应中的 `admission_error` 为
`delivery_provider_headers_unsupported`；不保存其值、不回滚其他目标、不退回老链路。
WebSocket 目标不使用 Provider Header，队列关闭的目标保留原行为。
允许值只存 delivery 内部投影 `provider_route_headers`，send-start 固化到 transport context；
旧记录缺字段按空处理。Worker、恢复后的 scope abort 和受管 Bot 回复接力复用原运行快照。
transport context 的 `relay_route_headers` 保留因果路由，`provider_route_headers` 只记录
实际 HTTP 路由；受管 WS 中间跳只保留前者，不把 Header 放入 WS frame，避免接力丢失 lane。
Inject 不覆盖承载 Send 的路由；配置收紧导致未发请求失败，不静默删除 Header 改路由。
scope abort 要求全部原运行路由（包括空路由）一致，精确 Provider abort 仍不支持。
Header 值不进入消息正文、模型、History、状态响应或日志。无需数据库 DDL 迁移；
回滚旧二进制前必须排空新增 Header 投影的队列，否则旧版本无法读取这些字段。
附件存于 bcs_messages.content.attachments，实际发送重新读取 canonical 消息；
历史、事件和状态不暴露保留的签名 URL。本轮不处理排队期间 URL 过期。

## 准入结果

原有群聊响应增加可选 `queue_admission`，不把排队当作 Bot 已收到：

```json
{
  "message_id": "message-id",
  "duplicate": false,
  "deliveries": [{
    "delivery_id": "delivery-id",
    "message_id": "message-id",
    "target_bot_id": "bot-id",
    "flow_kind": "group",
    "kind": "send",
    "status": "queued",
    "state_version": 1,
    "run_id": "logical-run-id",
    "wait_reason": null
  }]
}
```

一条 canonical 消息只有一份，每个目标有一条 delivery。容量拒绝仅影响对应目标，
状态为 `rejected_capacity`，其他目标仍提交。原有 delivered 数不包含尚未发送的目标。
提供 client message ID 的入口按 Session、发送者及该 ID 去重；响应丢失时查询原结果，
不要生成新 ID 重发。send 重试复用逻辑 run ID，但每次网络 attempt 使用新 request ID。
当前自动重试仅用于 send-start 后、transport I/O 前的运行索引注册失败；退避时间持久化，
次数跨重启保留，context 绑定不变。准备失败直接结束；调用 transport 后的错误/超时进入
Unknown，不使用此重试预算。TTL 到期不触发运行中请求的强制释放。

## 查询与取消

所有接口复用现有 Human/Bot 认证；当前 Session 成员资格、加入序号和消息可见性由应用层校验。
只有原发送者可以按消息或 delivery 取消。Admin/Integration 不隐式绕过成员权限。

| 方法与路径 | 参数 | 结果 |
| --- | --- | --- |
| `GET /openapi/v1/collaboration/messages/{message_id}/deliveries` | query `session_id` | `DeliveryStatusView[]` |
| `POST /openapi/v1/collaboration/sessions/{session_id}/message-deliveries/query` | `{"message_ids":["..."]}` 或 `{"client_msg_id":"..."}` | `DeliveryStatusView[]` |
| `POST /messages/{message_id}/deliveries/{delivery_id}/cancel` | `{"session_id":"..."}` | 每个目标的 `delivery` 和可选 `error` |
| `POST /messages/{message_id}/deliveries/cancel` | `{"session_id":"..."}` | 同上，覆盖该消息的全部目标 |

批量查询最多 100 个 ID，两种选择器不能混用。client ID 查询仅匹配调用者自己的原消息。
这些查询/取消接口使用 401、403、404、400、503 表达认证、权限、Session 不存在、无效参数和
服务故障；数据库详细错误不返回给客户端。不存在对应 delivery 时返回空集合，不伪造记录。
多目标取消可能部分成功，应检查每个目标结果；后续存储失败不表示前面已提交的取消被回滚。

queued 取消直接进入 `cancelled`，不调用 `chat.abort`。bound inject 只在 carrier 尚未开始
发送时允许撤回，并使旧准备结果失效。active WebSocket 请求先提交取消意图，再由有界
Abort worker 使用原连接 identity、原 session key 和实际下游 run ID 发送 abort。
active Provider 的精确 delivery 取消返回 `exact_abort_not_supported`，不会偷偷扩大范围。

现有 scope `chat.abort` 合并受管和原路径 active，不取消 queued/inject。Provider scope
必须匹配原 Provider/Bot 绑定和 session key，使用明确返回的 run ID 确认终止，空响应不是
成功证据。显式再次调用 scope abort 可以重新尝试 `cancel_unknown`，分配新的 abort ID；
调度器本身不会自动重发结果不明的 abort。全部网络 I/O 均在 abort-start 提交之后发生。

## 状态与通知

- send：`queued`、`dispatching`、`running`、`unknown`、`cancelling`、`cancel_unknown`，
  以及终态 `completed`、`failed`、`cancelled`、`expired`、`rejected_capacity`。
- inject：`pending_context`、`bound`、`consumed`，不独立启动 Bot，也不承诺模型采用了上下文。
- 等待原因：`prior_message_running`、`bot_capacity`、`rate_limited`、`bot_offline`、
  `retry_backoff`、`paused`。状态不明确时继续占用 lane 和 Bot active 名额。

后端 WebSocket 事件为 `message.delivery.updated`，`payload` 是同一状态 DTO。
只向应用层选出的当前可见 Session 成员发送，无匿名、整群或 run fallback 扩大投递。
这是提交后 best-effort 事件，不是持久化事件流；客户端按版本去重并通过查询校准。
Private owner 的 Human 代理读取仍以授权查询为准，不扩大私有消息实时广播范围。

IM 使用原消息来源和原 canonical Session：排队超过 2 秒提示，离线尽快提示，同一消息
多目标聚合；取消、失败和不确定状态提供简短说明。inject 和普通成功不额外提示。
通知有界、失败不回滚、不重试可能已发出的 IM 消息，重启不回放历史提示。

Provider 缓存过期后，只允许认证且原绑定一致的迟到终态通过 durable 状态进行核对，
不恢复来源未协商的流式输出。Group 运行没有对应 ChatRun 时不伪造 ChatRun；未接入消息表
的 Direct A2A 继续使用其原生命周期。

## 验证范围与运维边界

### 运行期数据库故障与终态保存恢复（2026-09-10）

2026-09-11 dev 合并兼容：队列准入和 run_reply/展示消息复用现有消息可见性分类，
同时持久化 visibility_domain/audience；状态查询和定向 WS 推送执行 Human 参与者
范围过滤。保留 dev 的邀请码及可见性迁移，队列迁移顺延为 MySQL 021–026、SQLite
022–027。已执行旧 Draft 迁移的测试库需要备份后单独核对，不自动改写其迁移记录。
合并回归：消息流、仓储、HTTP、WS release 测试 1116 通过、14 忽略；bootstrap
release lib 256 通过、5 忽略，认证默认值既有测试仍失败。未修改认证或运行数据库。

- 调度循环的扫描、精确读取及 CAS 操作遇到 Storage 错误时，保留当前工作，按
  100 ms 指数退避，最长间隔 5 s，数据库恢复后继续。不是重启整个循环，不清空在途
  send/abort 结果，也不重置限速历史。非存储类错误继续沿用原来的处理规则。
- 重试仅针对数据库操作；CAS 保留原 expected version。若 send-start 已提交但返回
  错误，重试遇到冲突后不能授权发送；保留原安全阻塞语义。绝不因保存失败再次调用
  `chat.send` / `chat.abort`。停止信号可中断退避；停止清理失败仍返回错误。
- 受管终态的初始关联查询、全文历史读取和终态事务允许同样的存储退避。保留原
  final/error/abort、规范化结果、回复 ID 和 CAS 版本，不重放整个 Bot 事件处理流程。
  终态事务提交不明确时，通过 CAS 冲突后的读库确认是否已完成，避免重复汇总或投递。
- Bootstrap 为共享 MessageFlow 安装弱自引用；接管的 chat 终态由独立任务完成，
  不随调用方取消而丢弃。每个实例最多接管 64 个并发终态，满额时入口等待，不无限
  创建后台任务。仅已获得名额的事件保证在原调用方断开后继续处理。
- 保存成功后才发送普通 frontend final/error/abort 和既有 IM 回调；辅助 run-context
  清理排在终态通知之后，不能因清理失败阻断已保存结果的显示。未提前报告成功，未新增
  Workbench 队列 UI 或新的前端事件协议。网络通知仍尽力而为，不自动重放 IM 写操作。
- 每个故障操作首次 WARN，持续失败最多每 30 s 再告警，恢复时输出一条摘要；不输出正文。
  永久存储错误同样暂停等待修复，不自动跳过坏数据或释放其 lane。初始配置/策略/存储
  检查仍 fail-closed；没有引入分布式接管或改变独立监控格式。

待保存终态只在当前进程内保留，停机或崩溃可能丢失；重启仍依赖原有 Unknown 恢复、
可信迟到事件或人工核实。本次没有新增 durable terminal inbox/outbox，也不承诺通知 exactly-once。

验证：消息流/仓储测试 410 通过，启动装配测试 4 通过。新增故障注入覆盖扫描、队首/
容量查询、等待原因、send-start/Submitted、abort-start/Aborted、恢复、终态关联/历史
读取/提交；验证已提交但响应丢失、不重复网络 I/O、断开调用方后的终态保存和 shutdown
中断退避。测试使用服务边界故障注入及真实内存仓储，另保留原 SQLite 装配回归；未模拟
真实 OceanBase 断网，未执行线上故障演练。

扩大执行 bootstrap release lib 测试：250 通过、5 忽略、1 失败。失败项
`config::tests::test_config_auth_chain_absent_falls_back_to_default` 单独运行也复现：
实际默认链为 `session`，测试仍期望 `agentpass/cookie/session`；HEAD 已存在该不一致，
本次未修改认证逻辑或该测试。`git diff --check` 及 store boundary 检查通过。

2026-09-08 DB 策略改造补充验证：Memory/SQLite 共享策略 CAS、版本冲突和失败不发布；
默认策略覆盖未列出的 Bot、动态降低并发/容量、间隔重算、TTL 不追溯；真实群入口及关闭
drain；管理 HTTP Human 鉴权及成功/失败审计；SQLite 默认关闭启动后动态开启及重启读取。
Workspace all-targets 编译和相关测试通过。架构 gate 仍有仓库既有 DEP 脚本、import、trait
命名及 conformance 映射失败，不能声明整体 gate 通过；未修改 gate 规则。
本次未重启用户 Singlebox，未直接更改其 SQLite 或本地运行配置，真实页面回归待用户执行。

本地测试覆盖 Memory/SQLite 事务、调度器、真实群聊应用入口、终态回复、取消、权限、
IM 聚合、scope abort、原连接绑定和关闭配置后的 SQLite 恢复。记录关联 ID、状态版本、
准入拒绝、attempt 和通知失败，不记录保留的附件 URL 或 Provider header。
状态提交日志携带 flow_kind、Bot/session，发送/终态记录等待及运行耗时。
专用队列监控日志见下一节；不新增 Workbench 运维面板。

真实 MySQL/OceanBase、Bot 引擎和 IM 账号需在部署环境执行验收；单元测试不能替代这些。
没有提供无证据强制释放 Unknown 的接口，必须取得可信终态、明确 abort 结果或完成隔离，
不能直接改表清除占用。Workbench UI、分布式部署、inject 压缩及 URL 刷新不在本版范围内。

## 单实例 worker 与监控（2026-09-08）

SQL worker 不再每 tick 加载全环境未完成记录。100 ms tick：queued Bot 游标批次 32、
每 Bot 合法 session 队首最多 8、过期最多 100、运行/取消控制 32、启动恢复 200。
新工作软预算 50 ms，发送准备并发 32、abort 并发 2；容量必须完整 COUNT。
准备完在按 Bot 的 mutations 锁/CAS 下精确复核顺序、容量、版本、TTL；同一 Bot 的
不同 session 共享锁，保留 Bot 级容量保护。终态回复同时锁定原目标 Bot 和全部回复目标，
Bot ID 去重并按字典序获取，避免交叉回复死锁。锁前读取仅发现不可变归属，锁内重新读取状态。
ACK、准入和等待原因更新使用同一套 Bot 锁；批量等待原因更新锁定该批涉及的 Bot。
锁目录仅短时维护 Weak 引用，不在目录锁内执行数据库 I/O 或等待 Bot 锁；闲置引用周期回收。
仓储事务写锁按 session 拆分，保护消息序号、幂等和事务组装；涉及 Send 的变更和准入
额外按目标 Bot 锁定容量，保留跨 session 的 queued 上限保护。先容量键、后 session 键，
统一排序去重；目录弱引用定期回收。开始持久化后，调用方取消不会提前释放 Bot/session 锁。
文件型 SQLite 使用 5 个连接，内存型仍为 1 个独立连接；SQL 在阻塞线程池执行。
写事务使用 BEGIN IMMEDIATE，事务全程占用同一连接。保持 WAL、foreign_keys 和 5 秒
busy_timeout，不降低同步持久化设置。SQLite 仍是单写者，不保证不同 Bot 并行写库。

### 日志量控制

正常准入、普通状态提交、消息落库和历史查询只输出 DEBUG；删除重复的 send-start INFO。
每条 Send 进入终态时输出一条 INFO 摘要，包含 message/delivery、Bot/session、run、终态
和可用的排队/运行耗时，不输出正文或附件。进入 Unknown / CancelUnknown 时输出 WARN，
不对每轮暂停扫描重复告警。异常、策略变更审计和独立 `message-delivery.log` 监控保持不变。

高频 `bcs_reply_profile` 分项计时默认关闭，即使全局日志为 DEBUG 也不自动打开。
控制台可通过 `RUST_LOG=bcs_reply_profile=debug` 或 logging tags/modules 显式开启；
文件输出需要该 output 的 level 为 debug（或更细）并在 targets 显式列出
`bcs_reply_profile`，仅 `*` 不开启它。独立压测 subscriber 不受生产日志过滤器影响。
终态回复事务语义不变，不支持多实例共享这套进程锁。
上下文按最新绑定 metadata 的有界结果逐条读正文，到条数/字节预算即停止；按上文规则
显式省略旧历史或安全截尾，不先加载全部绑定正文。

需先执行新增迁移 SQLite `024_delivery_worker_queries.sql` / MySQL
`023_delivery_worker_queries.sql`：索引和 `downstream_run_id` 派生查询列（从 transport JSON
回填，后续与 JSON 同事务更新）。旧二进制不会维护该列，不应在新旧版本间混跑。
Provider 本地缓存 TTL 30 秒，绑定 4096、配置及按 kind 的凭据各 1024；负缓存、singleflight、
提交后失效和旧加载失效保护。凭据不持久化到队列表、不输出日志；按 secret 的认证查询不缓存。
绕过应用直接改 DB 不保证立即生效，需等 TTL。发送前重新核对目标和当前 WS 连接。

### 独立队列监控日志（2026-09-09）

队列监控不再发布到 Prometheus，不依赖 `prometheus-metrics` feature 或 `metrics.enabled`；
其他 BCS Prometheus 指标保持原样。持久化队列启动即启用监控，无需增加业务策略字段。

通过现有 `logging.outputs` 声明独立输出，与其他日志使用同一套 `logging.rs`、
`RotatingFileWriter`、异步 `buffered_writer`、轮转/保留清理和 `LoggingGuard`。
不再有单独的文件线程、通道或文件生命周期。默认输出：

```toml
[[logging.outputs]]
name = "message-delivery"
path = "./logs"
file = "message-delivery.log"
level = "info"
rotation = "daily"
format = "raw"
targets = ["bcs_message_delivery_monitor"]
max_keep_days = 7
```

可以像其他日志输出一样配置 path/file/max_keep_days。旧配置明确提供 outputs 但没有此
输出时，bootstrap 为其补入正常 logging output，目录/保留天数取 main，其次第一项，
均无则 ./logs/7 天；清理任务也使用同一份生效输出列表。
`raw` 是新增的 message-only 格式，只写事件 message；既有 Text/Json 默认行为不变。
专用输出名 message-delivery 固定采用 raw 和专用 target，防止省略 format 的旧配置加上前缀。
其他文件输出及 console 强制排除此 target，即使 wildcard 或 RUST_LOG 也不会混入记录。
目录不可创建或文件打开失败会在日志初始化时明确失败，不静默丢失该输出。

当前文件为 message-delivery.log，每日通过既有日志轮转机制改名
message-delivery.log.YYYY-MM-DD；同名已存在时追加唯一后缀避免覆盖。
保留清理复用现有每小时 cleanup，max_keep_days=0 不清理。不要给其他 output 配置同一文件名。
后台立即采一次，此后每 10 秒 SQL 聚合，超时 5 秒。采集失败仍输出最后成功快照，
第 14 列为 0、第 15 列不推进；首次失败第 15 列为 0，零数值不能解读为已确认空队列。
成功空结果明确归零（含此前存在的深度分组）；失败时最老等待时长也是旧采样值，不继续外推。
没有消息/Bot/session ID、正文、URL、token、Provider binding key 或 header。
每个文件属于该进程配置的环境，监控采集端必须附加部署环境和实例标签，不能将不同环境文件混算。

### 固定 15 列协议 v1

英文逗号分隔，每行一条记录，无表头、字段名、空格前缀或 tracing 时间/level/target。
同一次采集输出多条记录，全部 15 列；第 4～9 列共同确定序列身份。编码 0 表示不适用/
未知（不得推断为某个已知类型）；新增编码或列需要同步更新采集配置，改变列含义必须升级版本。

| 列（从 1 起） | 含义 | 类型/单位 |
| --- | --- | --- |
| 1 | 格式版本，当前 1 | 整数 |
| 2 | 本轮采集结束时间 | Unix 毫秒 |
| 3 | 本次监控生命周期 boot ID，重启更换 | 32 位十六进制字符串 |
| 4 | 记录类型 | 整数，见下 |
| 5 | flow 编码 | 整数 |
| 6 | kind 编码 | 1 Send，2 Inject，0 不适用 |
| 7 | status 编码 | 整数；操作记录单独使用成功/失败编码 |
| 8 | wait_reason 编码 | 整数 |
| 9 | 指标/事件/操作编码 | 整数，随记录类型解释 |
| 10 | value | gauge 或累计数量，见下 |
| 11 | count | 累计时长样本数 |
| 12 | sum | 累计时长总秒数 |
| 13 | max | 本进程历史最大时长，秒 |
| 14 | 本次 SQL 快照成功 | 0/1 |
| 15 | 最后成功 SQL 快照时间 | Unix 毫秒；0 表示尚无成功样本 |

类型与 value 口径：

- **type=0 总览**：第 5～8 列为 0；code=1 queued Send、2 active Send、
  3 pending_context Inject、4 bound Inject、5 uncertain Send、6 最老 queued 秒数、
  7 不确定阻塞 lane 数、8 scheduler available（0/1）、9 paused（0/1）、
  10 policy version、11 writer 丢弃快照累计次数、12 文件写入/轮转/清理/flush 错误累计次数。
  1～10 为 gauge，11～12 为进程 counter；第 11～13 列为 0。
  active 包括 dispatching/running/unknown/cancelling/cancel_unknown，不等于模型正在计算。
  单 lane 一个 active 不变量下 code=7 等于 code=5。
- **type=1 深度分组**：code=0；value 为相应 flow/kind/status/wait_reason 当前条数；
  第 11～13 列为 0。wait_reason 为最近一次候选检查结果，不保证批外消息实时更新。
- **type=2 事件**：code=1 admitted、2 started、3 terminal；value 为提交成功后的事件累计数，
  通过 flow/kind/status 区分准入成功/拒绝及终态。admitted + rejected_capacity 是拒绝，
  不是成功接收；started 每次 send-start 都计数，包含安全重试。
  started 的 count/sum/max 描述创建到本次发送的排队秒数；terminal 描述最近 send-start
  到 terminal 的运行秒数；无起止时间的事件仍计入 value，但不计入时长 count。
  admitted 没有时长样本。Inject 绑定/consumed 不属于该 hook 的 terminal 事件；
  Inject 深度由 type=1 观察。重启恢复不会回放历史事件。
- **type=3 操作**：flow/kind/wait_reason 为 0，第 7 列独立定义 1 成功、2 失败。
  value 为累计返回行数/观察量，count 为调用次数，sum/max 为耗时秒数。
  code 顺序：1 lane_blocked、2 lookup、3 queued_bots、4 active_count、5 queued_heads、
  6 expiry_batch、7 control_batch、8 recovery_batch、9 queue_statistics、
  10 budget_exhausted、11 tick、12 bounded_contexts。
  active_count/lane_blocked 的 rows 为成功读取记 1，不是占用条数；tick/budget_exhausted
  的 rows 为该次 worker 工作集合大小。budget_exhausted 的 count 表示软预算/任务槽耗尽次数。
  queue_statistics 超时被取消时可能没有产生完成 hook，须结合第 14 列和错误日志判断。
- **type=4 Provider cache**：第 5～8 列为 0；code=1 hit、2 negative_hit、3 miss、
  4 error、5 invalidation、6 load；value 为进程累计次数，其余统计列为 0。
  negative_hit 是 hit 子集，不能重复加到命中率分母。缓存累计从进程启动开始。
- **type=5 Inject 历史选择**：第 5 列 flow，第 6 列 1 Send，第 7～8 列 0；
  code=1 选中历史累计条数、2 拼装历史累计 UTF-8 bytes、3 舍弃历史累计条数、
  4 发生整条省略或单条截尾的 Send 累计次数。第 11～13 列为 0。
  仅首次 send-start 提交（attempt_no=1）计数，安全重试不重复计数；这些是发送选择量，
  不是模型实际使用量或已完成消费量。进程重启不回放历史；准备失败/取消前未 send-start 不计。

通用 flow 编码：1 group、2 direct_a2a、3 task、4 system、5 state_machine。
通用 status 编码：1 queued、2 dispatching、3 running、4 unknown、5 cancelling、
6 cancel_unknown、7 completed、8 failed、9 cancelled、10 expired、11 rejected_capacity、
12 pending_context、13 bound、14 consumed、15 discarded_context。
wait_reason 编码：0 无/未知、1 prior_message_running、2 bot_capacity、3 rate_limited、
4 bot_offline、5 retry_backoff、6 paused。

例子（实际日志只包含下面的数据行，不包含解释）：

```text
1,1788912000000,0123456789abcdef0123456789abcdef,0,0,0,0,0,1,3,0,0,0,1,1788912000000
1,1788912000000,0123456789abcdef0123456789abcdef,1,1,1,1,6,0,3,0,0,0,1,1788912000000
```

第一行表示 queued Send 总量为 3；第二行表示 group/Send/queued/paused 分组为 3。

### 采集与调参

同一 boot ID、同一序列的累计 value 差值 / 采样时间差可算发送/完成/拒绝/过期速率；
时长平均值为 sum 差值 / count 差值（分母 0 时无样本）。
这些是累计 sum/count/max，**不是直方图，不能计算 P95/P99**；max 是启动以来峰值，
不能用相邻 max 差值当区间最大值。新 boot ID 重置 counter，第一条只建立基线。
type=2/3 某个组合第一次事件前不输出，首次出现的累计值从本 boot 的 0 起算。
第 14～15 列只描述 SQL 快照；策略/worker/cache 数据仍为本轮读取的数据。

writer 复用现有 tracing_appender 非阻塞输出，缓冲上限 4096、lossy=true。每轮快照作为
一个多行日志事件提交，满载丢弃并累计计数，不阻塞调度或事务；文件 IO 错误通过日志系统
标准错误输出报告（避免在 writer 中递归打日志），后续成功记录可观察错误累计。
监控需同时告警文件停更、快照采集失败及陈旧、writer dropped/errors 增长。
普通关闭先停止调度并等待 sampler 退出；服务生命周期结束时现有 LoggingGuard 统一
flush 所有日志 worker。进程被强杀可能丢失尚未落盘的监控，
不会改变消息事务结果。该日志不是审计账本，不保证 exactly-once。

调参前先检查 paused、offline、uncertain，再看排队年龄、发送/完成速率与运行耗时。
active 接近 max_running 且运行慢，不应仅增加 worker 批次；主要等待 rate_limited 时检查发送间隔。
持续 budget_exhausted 才结合仓储操作耗时和 SQL 执行计划评估批次；不自动改变任何生产策略。
逐 Bot 排障继续关联常规结构化日志及已有 delivery 查询。

### 本次验证范围

2026-09-09：logging 相关 9 个测试、delivery 相关 10 个测试、bcs-config-api 的 48 个测试
全部通过；默认 feature all-targets check、生产 lib no-default-features check 和 diff 检查通过。

验证包括固定 15 列、无字段名前缀、快照失败保留/空结果归零、事件时长口径，以及通过
现有 raw layer / async writer / worker guard 真实写入临时日志目录、与普通文件/console
隔离、共享轮转清理、默认及显式 logging.outputs 兼容。编译覆盖默认 feature 和生产 lib
no-default-features；测试自 dev-dependency 会合并默认 feature，无 Prometheus 以生产
lib check 为证据。未重启运行 BCS，未验证生产采集器、磁盘故障压测或真实 MySQL/OceanBase。
