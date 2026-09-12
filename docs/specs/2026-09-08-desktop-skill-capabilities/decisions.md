# Desktop Skill：最终决策摘要

状态：Q1–Q29总体方案及统一恢复约束已定稿；业务待实现。本文件提取最终选择和理由，原始探索中的未采纳候选不作为实施指令。详细目标合同及验收以[spec.md](spec.md)为准。

## 已定选择

| 决策 | 最终选择与理由 | 规范落点 |
| --- | --- | --- |
| Q1 范围 | Skill相关OpenAPI/市场/工坊能力适配Desktop；OpenClaw/Hermes主验收，其它已有Engine保留受影响兼容。不扩成所有Bot平台API。 | Solution、D2 |
| Q2 升级 | 新Desktop Center及bytes能力可要求客户端/Engine更新；原有能力兼容，不要求云端统一重启。 | D10 |
| Q3 与#455关系 | 复用逻辑交付seam，但不等待#455全部模块；不以本次适配改DB locator或强制切Pool。 | D5/D10 |
| Q4 内容来源 | Backend触发，Engine按需取得当前有效exact内容；不启动时全拉Center市场。 | D3/D6 |
| Q5 成功边界 | Publication、Reference添加、缓存Ready、Runtime生效分别表达；Runtime降级不回滚合法Desired State，文件上传/参数写入另守实际成功合同。 | D1/D4 |
| Q6 Hermes创建 | 修正式Local Bot Workflow的Hermes政策缺口，不据枚举存在就开放所有Engine。 | D4、A01 |
| Q7 恢复方向 | Backend通用任务重读最新Reader并投影；Engine只准备缓存，不在下载完成回调激活旧目标。 | D8 |
| Q8 部分结果 | Mixed Local/Repo/Center逐项best-effort；一个等待项不拦其它可执行项。旧DTO整批拒绝是明确兼容例外。 | D7/D10 |
| Q9 唤醒 | 启动/变更/Track Latest主动触发、任务跟进和低频扫漏进入同一恢复机制；健康扫描状态更新不是已有投影保障。 | D8 |
| Q10 频率 | 新due-now任务及时唤醒；正常内容等待5秒Reschedule；异常原退避；约10分钟扫漏。周期不是SLA。 | D8 |
| Q11 完成判据 | 看逐项可恢复工作，DEGRADED不得掩盖PENDING；停止重试不代表CONVERGED。 | D8 |
| Q12 新旧版本 | V2未Ready保留V1有效链接；切换使用新一轮最新期望，不保证旧MCP环境也保持。 | D9 |
| Q13 退出KISS | 前端离线禁SkillSet变更，Backend不新增Desktop政策。沿用显式retired，不新增退出账本、链接回执或整目录接管；接受历史retired遗失限制。 | D9 |
| Q14 派生包 | Canonical exact生成可复用ZIP，冷包重工作在后台；独立分发namespace，不复用Draft/staging字段，不新增业务Version。 | D6 |
| Q15 下载描述 | Backend签发精确对象描述，Engine直连OSS；大包不经BaaS、不直连SC，不公开私有meta或长期凭证。 | D6/D7 |
| Q16 深Module | 共用Reader/Resolver/Projector/apply；Engine通过Mounted/Downloaded Adapter准备内容；Backend新差异集中SkillRuntimeDelivery。 | D5 |
| Q17 URL期限 | 一小时签名，过期后正式投影刷新；URL不是内容身份，过期不删除Ready缓存。 | D6 |
| Q18 Ready | 首次完整校验与安全落盘后才可用；命中轻量检查。缓存元信息不是链接所有权回执，也不证明所有用户篡改都可检测。 | D6 |
| Q19 缓存清理 | 只清理确认归属且已结束的临时工作；不做完整缓存TTL/LRU/keep-N或云端派生包GC，磁盘风险可见。 | D6 |
| Q20 工作项生命周期 | 单Bot一个live工作项，30分钟单轮；重复enqueue不合并payload/加速/续期。保留既有Queue窗口，由扫漏兜底，不加outbox/generation/全局锁。 | D8 |
| Q21 MCP过渡 | Skill与MCP继续独立best-effort，保留V1链接不承诺V1业务零中断；不临时授权旧MCP依赖。 | D9 |
| Q22 apply合同 | 复用一次逻辑apply，可选下载描述与实际Adapter evidence；不新增日常probe/verify。Backend按可信bot_type、Engine按既有部署配置集中选择。 | D5/D7 |
| Q23 旧端保护 | 只在明确合同拒绝时提示升级；未知结果不换写协议。旧DTO混合请求整批拒绝不拆批补发；合法DB期望保留。 | D10 |
| Q24 Center查看 | 云端/Desktop共用Canonical精确内容读取，不看设备Ready、不触发下载；期望内容与实际运行内容区分。 | D4 |
| Q25 参数最小修复 | 保留接口、name键JSON和历史地址；读失败零写、写失败失败。不新增参数API/DB/CAS锁/自动注入；原生消费另列未验证。 | D4 |
| Q26 MCP保持现状 | 只新增Skill恢复，后续轮次零MCP/Passport重推；原MCP故障处理保留，不新增第二个MCP任务。 | D9 |
| Q27 bytes | 修Desktop内部请求/响应bytes保真，保留BaaS base64和旧文本wire；不新增上传系统。 | D4 |
| Q28 完整验收 | 包含市场SC批量Reference→云物化→正式Set/Installation→Desktop下载→实际软链，并覆盖工坊、Local/Repo及共享辅助入口。 | D3、A01–A34 |
| Q29 分组与统一恢复 | 四组分工；所有Desktop Skill入口共用一个Bot级恢复Module/task type，不能每入口各建一套。 | D2/D8、Plan |

## 统一恢复的具体边界

1. `ac_task_queue`负责持久执行和调度，G4负责一套Desktop Skill业务Handler；旧activation_sync骨架不是现成实现。
2. 所有触发源只通过共同Interface确保Bot恢复工作；按Queue作用域与owner+bot去重，不按Skill/Set/Reference/version分别排长期恢复任务。
3. Worker准备缺包后重读当前Reader与绑定，再调用共同Projector；正常apply同时观察内容就绪状态并推动映射，不再单独查百分比进度。
4. 市场物化后Track Latest可能发生在最终add之前，故正式add及共同变更收尾必须接入；Bot未就绪/snapshot失败的提前PENDING也不能漏接。
5. inactive Set的SKIPPED、权限/DB失败不是自动下载或重放业务命令的理由。Reference COMPLETED不改定义为所有设备Ready。
6. 跨入口合同测试必须证明：同Bot由市场、Set和Track Latest共同触发仍只有一个live恢复任务，读取最新目标；不能只提供三条独立入口成功测试。

## 明确保留的限制

| 限制 | 后续处理原则 |
| --- | --- |
| 遗失retired可能留下旧链接 | 不通过新扫漏推断全部链接归属；正常受支持取消场景必须通过窄测试，发现不足只评估最小共享修复。 |
| 入队与最后检查窗口、有限任务期限 | 记录真实失败并低频重新ensure；不宣称强一致或固定恢复秒数。 |
| 参数原生消费未证实 | 存储/回读与原生执行分别验收，未验证不宣传已完成。 |
| 旧MCP过渡及原MCP重试覆盖 | 保持既有行为，不掩盖失败，不扩成本期MCP治理。 |
| 完整缓存增长 | 记录容量/下载错误；不未经授权清理在用或用户内容。 |

所有源码审计均为固定提交证据。文档定稿不表示实现、CI、评审、合并、部署或实机验证已经通过。

## 现场验收补充决策

### 术语边界

- **Desktop Device Offline**：当前 Desktop Runtime 没有活跃设备可接入，由 Bot `status=OFFLINE` 或可信的 BaaS 结构化 `NO_ACTIVE_DEVICES` 事实表达。它不是 Skill 资产下线，也不是 Runtime 代码错误。
- **Transient Runtime Failure**：无法证明设备离线的 timeout、5xx、非法响应或连接故障；当前 Task 仍可按故障策略退避重试。
- **Public Recoverability**：公开 `RuntimeProjectionIssue.retryable` 表达未来再次投影是否可能恢复，不表达当前 Queue Task 应继续执行。
- **Recovery Continuation**：Desktop Recovery 深模块对当前 Task 的内部调度决策，包括等待事件后结束、故障退避、进度跟进和永久停止；不从公开 `retryable` 直接推导。

### R1. 明确离线与暂时通信故障分离

2026-09-11 预发发现：保留历史 Binding 的明确 OFFLINE Desktop 被恢复扫漏持续拉起，BaaS 返回 `NO_ACTIVE_DEVICES` 后又被泛化为可重试的 `SKILL_RUNTIME_UNAVAILABLE`。因此补充下列领域分类：

- Bot 当前状态明确为 `OFFLINE`，或 BaaS 明确返回结构化 `NO_ACTIVE_DEVICES`，均表示 **Desktop Device Offline**：这是等待外部上线事件的正常状态，不是当前任务可通过高频重试修复的 Runtime 故障。
- timeout、5xx、非法响应及无法确认设备状态的连接异常仍表示 **Transient Runtime Failure**，继续允许 TaskQueue 使用既有故障退避。
- Device Offline 对外保持 Runtime 尚未收敛的 `PENDING` 事实，并保持 `retryable=true`，表示未来再次投影可以解决；该字段不决定当前 Queue Task 的调度。当前任务结束，后续由设备上线/重连事件重新确保同一 Bot 恢复，低频扫漏只负责漏事件兜底。
- 明确离线不得记录为 Engine apply 失败，也不得产生 ERROR traceback；具体 Sweeper/Handler 调度与日志级别在后续 R2/R3 决策中确定。

该补充不改变 Installation Desired State，不回滚已提交的 SkillSet/Direct 变更，也不新增 Desktop 专属业务拒绝或第二套恢复任务。

### R2. 明确离线终止当前恢复任务

- Sweeper 只排除明确 `status=OFFLINE`，不收窄成仅扫描 `ACTIVE`，避免误伤仍可能具备设备连接的过渡状态。
- Handler 在开始执行和正式 apply 前都重新读取 Bot；若此时已明确 OFFLINE，当前任务返回 `Complete`，不访问 Engine、不继续高频 Retry。
- 执行期间 BaaS 明确返回 `NO_ACTIVE_DEVICES` 时同样结束当前任务。不能用十分钟 `Reschedule` 长期占用 live key，因为重复 `ensure` 不会提前既有任务的 `run_at`，会延迟真实重连后的 due-now 恢复。
- 设备上线/重连事件重新 `ensure` 当前 Bot；若事件丢失，Bot 状态恢复为非 OFFLINE 后由下一轮约十分钟 Sweeper 兜底。

### R3. 明确离线的 Runtime 结果合同

明确离线使用稳定问题码 `DESKTOP_DEVICE_OFFLINE`：Runtime `status=PENDING`、该问题 `retryable=true`，原因表达“能力状态已保存，将在设备上线后自动同步”，建议动作是启动或重新连接 Desktop 客户端。`retryable=true` 遵守公开 DTO 的原义：未来一次投影可能解决；它不授权当前 Task 高频重试。

该结果继续与成功提交的 Desired State 一起返回，不改变公开 HTTP 结构、不回滚 Installation；不能再用通用 `SKILL_RUNTIME_UNAVAILABLE` 把明确离线误报成平台或 Engine 故障。

### R4. 离线恢复日志分级

- Sweeper 跳过明确 OFFLINE 时不逐 Bot 记录日志，只保留轮次汇总 INFO/指标。
- Handler 在执行前识别明确 OFFLINE：INFO，无 traceback。
- BaaS 返回结构化 `NO_ACTIVE_DEVICES`：若 DB 同为 OFFLINE 则 INFO；若 DB 仍显示在线则 WARN，表达短暂状态不一致；两者均不打印异常 traceback。
- timeout、5xx、连接中断等状态未知的外部异常：WARN，并保留 TaskQueue 故障退避；避免在多层重复打印相同 traceback。
- 非法响应、身份/绑定不变量破坏或已进入 Engine 后的 apply 异常：ERROR，并保留 traceback。

日志通用原则为：正常业务状态用 INFO，可恢复外部异常用 WARN，代码错误、合同破坏和不变量失败用 ERROR。明确离线必须在进入 `PerDomainRuntimeProjection` 的通用 ERROR 分支前转成结构化 Runtime 结果。

### R5. 自然重连使用 Runtime Reprojection 事件

Desktop 健康扫描确认 `OFFLINE → ACTIVE` 并提交状态更新后，发布既有 `RuntimeProjectionRequestedEvent`。不复用只表示首次激活的 `DeviceActivatedEvent`，避免重复触发首次初始化消费者；健康扫描也不直接依赖 Skill Recovery Service。`SkillSymlinkListener` 继续把该事件交给统一 `DesktopSkillRecoveryService.ensure()`，创建 due-now 工作。

事件发布失败不得回滚已经确认的在线状态，但必须可诊断；低频 Sweeper 在 Bot 已恢复为非 OFFLINE 后承担漏事件兜底。

### R6. Desktop Skill Recovery 状态资格

- `ACTIVE` 与 `PENDING` 可进入恢复。PENDING 请求若 BaaS 明确无设备，则结束当前任务并等待首次激活事件。
- `OFFLINE`、`FAILED`、`RELEASING`、`RELEASED` 不创建高频恢复工作；已有 Handler 观察到这些状态时返回 `Complete`。
- 未知状态不静默视为 ACTIVE：记录 WARN 并跳过/结束本轮。

资格判断收敛在 Recovery 深模块的单一内部规则，由 `ensure()`、Sweeper eligible 统计和 Handler 开始/正式 apply 前的二次复核共同使用；各业务入口不得重复状态判断。

### R7. NO_ACTIVE_DEVICES 的结构化边界转换

- BaaS Service 负责解析 HTTP/JSON，保留结构化 `status_code` 与 `error_code=NO_ACTIVE_DEVICES`，不把它压成只能匹配的字符串。
- `BaasConnInfoBuilder` 把 provider-specific 错误转成 provider-neutral `DeviceOfflineError`；其它权限、绑定、协议和连接失败继续使用既有错误语义。
- `PerDomainRuntimeProjection` 在通用异常分支前捕获 `DeviceOfflineError`，生成 R3 的 `DESKTOP_DEVICE_OFFLINE / PENDING / retryable=true`；当前任务是否继续由 R16 的内部 continuation 决定。
- Recovery Handler 只消费结构化 Runtime issue，不感知 BaaS 响应文本或 provider 实现。

BaaS 的明确无设备记录 INFO；PerDomain 若 DB 仍显示 ACTIVE 则记录单行 WARN 表达状态竞态，若 DB 已 OFFLINE 则 INFO，均不打印 traceback。不得将整个 `ConnInfoBuildError` 降级，因为其中仍包含真正的合同和基础设施故障。

### R8. 状态提交后发布 best-effort 重投影事件

健康扫描确认 BaaS 在线后，先更新 Bot/Binding 为 ACTIVE，再发布 `RuntimeProjectionRequestedEvent`。Listener 按事件 `binding_id` 重新验证当前绑定，避免迟到事件投影到旧设备。事件继续使用非 required 的进程内发布；失败记录错误但不回滚在线状态、不增加 outbox，由后续健康扫描和 ACTIVE Bot Sweeper 兜底。

### R9. 多 Pod Sweeper 不新增分布式锁

允许多个 Backend Pod 重复执行低频扫描，继续依赖 Queue 的 `env + app + task_type + owner/bot` live-key 保证同一 Bot 至多一条 live recovery task。OFFLINE 过滤消除主要风暴后，本期接受少量重复 SELECT/ensure；只有实际指标证明扫描成为瓶颈时再单独优化，不为本修复引入锁生命周期。

### R10. 聚合观测优先于逐 Bot 正常日志

每轮 Sweeper 记录聚合 INFO/metric：`scanned`、`eligible`、`skipped_offline`、`skipped_terminal`、`skipped_unknown_status`、`task_created`、`task_joined_existing`、`ensure_failed`、`duration_ms`。普通 OFFLINE 不逐 Bot 记录；状态竞态、未知状态和持久化失败才带 owner/bot/binding 形成单项日志。

Task outcome/监控使用稳定原因分类，如 `DEVICE_OFFLINE_WAITING_FOR_RECONNECT`、`TRANSIENT_RUNTIME_FAILURE`、`POOL_TRANSITION_WAITING`、`CENTER_CONTENT_DOWNLOADING`，不得解析异常文本聚合。

### R11. 重连唤醒不升级为强一致事件事务

最终保证由“已提交在线状态 + best-effort Runtime Reprojection event + Queue 持久任务 + ACTIVE Bot 低频 Sweep”组合提供。事件 handler 不设 required，不把 Skill enqueue 失败反向耦合为健康状态更新失败；事件失败保留 ERROR，Sweep 提供最终兜底，但不宣传固定恢复 SLA。

### R12. BaaS 明确无设备使用专属结构化错误

新增 `BaasNoActiveDevicesError` 作为 `BaasServiceError` 的窄子类，保留 `status_code` 与稳定 `error_code=NO_ACTIVE_DEVICES`；不扩大所有既有 `BaasServiceError` 的构造协议，也不把 HTTP Response 透传到 Runtime。`BaasConnInfoBuilder` 将其转换为 `device_context.py` 中 provider-neutral 的 `DeviceOfflineError`，其余 BaaS/Binding/协议错误继续走现有失败类型。

### R13. Recovery 使用内部三态资格分类

不复用回答“Bot 是否可对外服务”的 `is_bot_ready()`。Desktop Recovery 内部定义纯分类 `DesktopRecoveryDisposition`：`RUN`、`WAIT_FOR_DEVICE`、`STOP`。ACTIVE/PENDING 映射 RUN，OFFLINE 映射 WAIT_FOR_DEVICE，FAILED/RELEASING/RELEASED/未知映射 STOP（未知同时 WARN）。`ensure()`、Sweeper 和 Handler 二次复核共用该规则；WAIT/STOP 都结束已有任务但分别计量。

### R14. 自然重连事件由 DesktopBotService 发布

`DesktopBotService._apply_decision()` 完成 `OFFLINE → ACTIVE` 状态更新后，通过窄 helper 重新读取当前 Binding 并发布已有 `RuntimeProjectionRequestedEvent`。不在 Health Scanner 拼 Event、不创建第二种 Event DTO、不直接调用 Skill Recovery，也不对 `ACTIVE → ACTIVE` 心跳重复发布。部分状态更新或迟到事件由 Listener current-binding 校验和 Recovery disposition 安全拒绝，下一轮扫描继续收敛。

### R15. 正确性修复不新增行为开关

不新增 `skip_offline`、日志级别或重连事件开关，避免产生只修一半的配置组合。现有 `desktop_skill_recovery.enabled` 仍只作为整套 Sweeper 的紧急控制；明确离线分类在所有环境一致生效。发布以预发任务/日志下降和真实重连自动恢复为门禁，回退使用代码版本回滚。

### R16. 可恢复性与当前任务调度分离

公开 `RuntimeProjectionIssue.retryable` 只回答“未来再次投影是否可能解决”，不能被 Backend 或前端解释成“当前 Queue Task 应立即重试”。Desktop Skill Recovery 内部使用独立 `RecoveryContinuation` 分类：

- `DESKTOP_DEVICE_OFFLINE`：`COMPLETE_WAITING_FOR_EVENT`；公开仍为 PENDING/retryable=true。
- timeout、5xx 等状态未知的暂时故障：`RETRY_WITH_BACKOFF`。
- Center 内容正常准备或下载中：`RESCHEDULE_FOR_PROGRESS`，维持五秒进度跟进。
- 永久包/合同问题：`COMPLETE_PERMANENT`，保留 DEGRADED/non-retryable。

该修订不增加公开字段或 code 枚举，不改变 Gateway schema。现有调用方可继续只处理 PENDING；前端可选识别 `DESKTOP_DEVICE_OFFLINE` 优化文案，但不得仅因 `retryable=true` 做秒级命令重放。

### R17. 只按可信结构化错误识别离线

仅在可信 BaaS 非2xx响应可解析为对象且 `detail.error` 精确等于 `NO_ACTIVE_DEVICES` 时进入专属离线分支；不匹配异常字符串，不要求固定为单一HTTP状态。401/403、其它code、坏JSON及缺字段继续走普通错误。现网404和历史约定503都需测试，避免状态码演进重新制造错误风暴。

### R18. 测试必须覆盖完整转换与竞态

修复测试需贯通 BaaS解析、Builder转换、PerDomain结果/日志、Recovery资格、Sweeper、Handler两次状态复核、既有live任务排空和自然重连事件。必须证明其它4xx/5xx/timeout仍按原故障语义、Center正常下载仍五秒跟进，并分别用OpenClaw/Hermes实机验证离线不访问Engine和上线后无需用户二次操作即可恢复。

### R19. 预发以任务风暴停止和真实重连为门禁

部署后允许既有任务执行一轮并Complete；稳定后明确OFFLINE样本不再新增高频恢复任务、不再由恢复链请求BaaS ws-info、不再产生PerDomain ERROR traceback。OpenClaw/Hermes各验证一次OFFLINE→ACTIVE事件、due-now任务、Engine apply、正确exact软链及可读SKILL.md时间线。5xx/timeout退避和Desired State不回滚同时回归；不承诺固定端到端秒数。

### R20. 文档落点与ADR边界

更新正式Spec D8/D9/Testing、本文决策账本、统一验收Issue #2105，并在实现PR同步Skill Center AGENTS/README的已实现行为。当前不新建ADR：这是既有Desktop Recovery合同的现场修订，Spec与决策账本已经是权威记录；避免产生第三份重复事实源。

### R21. 一个Avernet修复PR，OCB只做集成gitlink

BaaS结构化错误、provider-neutral错误、Runtime结果/日志、Recovery资格/调度、自然重连事件、测试和实现文档必须作为一个Avernet原子切片交付，避免只修一半的部署组合。不新增OCB功能实现；Avernet合入后通过统一gitlink更新和Corp集成测试交付。公开DTO形状不变、code为自由字符串，因此不重新生成Gateway OpenAPI artifact。
