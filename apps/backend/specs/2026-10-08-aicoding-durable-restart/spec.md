# 普通 Coding Bot 持久化异步重启

- 日期：2026-10-08。
- 所属模块：Backend / bot_management / engines / aicoding。
- 基线：OCB `0974a4af1c`，固定 Avernet `94bae68a6`。
- 相关契约：`engines/aicoding/restart-backup.md`、
  `specs/2026-09-10-aicoding-restart-template-snapshot/spec.md`、
  `specs/2026-08-04-task-queue-idempotency/`。
- 沿用已有引擎策略边界、任务队列和 ext CAS，不引入新的系统级 ADR。

## 范围

只有明确的 `active_engine=aicoding/claude_code` 的普通 HTTP/OpenAPI 重启
入口改为持久化后台任务。非 Coding 引擎原执行方式不变。已发布服务 Bot
重启、Caller 恢复仍使用原来的 `prepare_restart_async`，不创建本任务。
同步 `restart_bot` 的参数不变，仍可被原直接调用方调用。

不新增表、列、任务查询接口或前端代码。前端仍每 3 秒查询现有状态接口，
默认 5 分钟停止等待；这不取消后台任务。队列必须启用并注册本 handler；
不可用时拒绝受理，不退化成不可靠的临时后台线程。

## 受理和持久化

1. HTTP 原有权限/所有权检查不变。策略读取 Bot，检查引擎、支持的生命周期、
   desktop 排除、entity_id 和现有绑定的可定位性。
2. 任务按 `(tenant, owner_id, bot_id)` 构造固定长度去重 key，队列原有
   `(env, app, task_type, active_idempotency_key)` 唯一约束是去重权威。
3. 新任务 payload 冻结 operation_id、引擎、绑定、逻辑设备、provider、
   原 Bot 状态、tenant、nick_name 和受理时间。模板 extra_configs 不写任务表，
   而是通过既有策略模板保存/加密契约处理；旧的 best-effort 失败行为保留。
4. 使用 Bot ext CAS **同一条 UPDATE** 写入 `coding_restart` 和 `status=PENDING`，
   并校验读取时的 status/binding_id/active_engine。保留旧绑定、设备 ID 和其他 ext。
5. 返回原有 Bot-shaped response，附加既有 `restart_in_progress` 标记和
   `restart_operation_id`；普通 HTTP route 沿用其已有的 202 分支。
   OpenAPI 和其他适配器不修改既有 envelope/HTTP status 契约。

任务先入队、再写状态，两者不是跨表事务。Worker 和请求线程都能通过相同
CAS 初始化 journal；队列入库失败不写 PENDING。若任务已入库但 journal 写入
失败，请求不能报告成功，任务仍可能在存储恢复后执行。重试请求加入原任务，
不会重复执行。更大的 task row ID 防止延迟请求覆盖后续操作。

## 引擎内 journal

`Bot.ext.coding_restart` 保存 operation_id、task_id、phase、error_message、
绑定、开始时间以及 provider handoff。阶段为：

`QUEUED -> BACKING_UP -> RESTARTING -> WAITING_READY -> SUCCEEDED / FAILED`

更新必须匹配 operation_id 和允许的当前阶段，并重读 ext 后 CAS，不能覆盖
其他字段。任务 handler 恢复受理时的租户作用域，结束后清理。

受理提前设置 PENDING 后，原同步生命周期仍需知道受理前的 ACTIVE/FAILED
状态（尤其 FAILED 且无绑定的历史 provider 恢复）。引擎专用执行上下文为
原生命周期提供该验证快照，不回写旧状态。原同步流程的 provider、绑定、
状态等进一步校验仍在 Worker 中进行；失败通过状态接口反馈。

## 执行及恢复

- Worker 在线程池执行既有同步生命周期，不阻塞服务端事件循环。
- 备份使用固定 operation_id，备份中断后再次投递仍使用同一 helper 操作。
- 备份在既有短时重启锁外等待；获锁后，先执行原凭据/身份复核，再 CAS
  `BACKING_UP -> RESTARTING`。重复投递不能二次跨过销毁/替换前的 fence。
- `RESTARTING` 的重复投递只等待，不释放去重 key，不重复 stop/start。
- BaaS 原地重启在原 provider intent 持久化后、远端 mutation 前记录 handoff，
  保存 binding/request_id/workflow baseline；拿到 publish ID 后补入 journal。
  丢失响应时沿用已有 BaaS 持久化 poller 的 workflow adoption，不重新提交。
- 成功必须核对本次 publish 的 SUCCESS、当前绑定和真实 Bot readiness，
  不能把旧实例上报的 ACTIVE 当成本次重启成功。
- stop/start 分支在 stop 返回后、异步 start 前记录 allocation handoff，
  等待新的绑定及就绪；不把旧绑定当作完成证据。
- 后台业务观察预算为 2 小时，队列 deadline 为 24 小时；现有每目标备份
  1500 秒预算不变。业务超时后记录失败，禁止通过超时放行重启。

**明确的安全边界：**既有普通 stop/start 不是外部平台的事务性 exactly-once
接口。在 mutation fence 与 durable handoff 之间进程崩溃，不能证明副作用
是否发生，因此保留进行中直到观察预算结束，随后报告需要检查实例状态，
不盲目重放。allocation handoff 后进程丢失也可能无法完成创建，最终报告
超时；本变更不虚构底层平台不存在的自动恢复保证。已记录 BaaS handoff 的
任务继续通过原 provider poller 恢复。发布/Caller 路径不受该编排影响。

## 状态查询兼容

普通 `/api/bots/{id}/status` 和 OpenAPI status 完全沿用基线实现，调用
`get_bot` 读取数据库和绑定。不新增查询方法或策略投影，不读取任务队列。

- 受理原子写入 PENDING，并清除上一轮 start_status/start_message。
- 后台失败原子写入 Bot.status=FAILED、ext.start_status=FAILED、
  ext.start_message=脱敏原因；普通状态接口按原逻辑返回 error_message。
- 写失败前校验引擎及目标绑定；不把替换后的实例标记失败。
- 成功就绪沿用原容器启动/provider 回写，不以重启任务 journal 强制覆盖查询结果。
- journal 内错误只用于操作审计/恢复，不用于覆盖接口结果。

本次不改公共设备回调。ACTIVE 绑定再次收到 alive 不会触发 Bot 的
PENDING→ACTIVE 更新；仅绑定自身 PENDING→ACTIVE 时触发。尚未证实存在
本次异步改造特有的迟到回调冲突，不以该假设增加公共写入钩子。
既有 startup 回调仍能修改 start_* 字段，本次不承诺隔离所有历史回调。
若任务被队列直接终止而 handler 未执行失败落库，Bot 可能保留 PENDING；
查询侧不再合成失败，前端仍按原 5 分钟超时退出。此情形需运维核对任务
与实例，不自动重复容器副作用。worker 正常执行的业务超时仍落库 FAILED。

## 传播与部署

- 公共层只提供策略 dispatch、provider handoff 以及通用 CAS 扩展；具体引擎名、
  journal 字段、备份、任务、去重及状态映射均在 aicoding 策略模块内。
- Repository 新增可选 CAS 参数，旧 ext-only 调用语义不变；无需数据库 migration。
- BotService 查询契约和两个 status 路由恢复基线，不增加状态读取扩展点。
- DI bootstrap 注册 handler，TaskWorker startup 后才领取任务。
- 上线应保证领取队列的所有实例都注册新 task_type，再启用新入口；混部旧 worker
  可能把未知任务判为失败。回滚前应排空/处置本类型未结束任务和 journal。
- 对已提交副作用的失败，不得简单清空 journal 后自动重放；先核查原 provider
  workflow 和实例，确认恢复方案。

## 验证

测试覆盖策略分流、受理不执行备份、并发去重、真实 SQLite 的状态/ext 原子 CAS、
任务先入队后的恢复、稳定 backup operation ID、锁内一次性 fence、备份失败和
超时、目标/引擎变化、旧 ACTIVE 和旧启动错误、provider handoff/丢响应恢复、
真实 BotService BaaS 路径、租户隔离、状态接口字段兼容、生命周期发现及其他
引擎/发布/Caller 回归。

未使用生产 Bot；真实容器停写、NAS 备份、平台部署和浏览器全链路尚需预发验证。
本次本地验证：**1569 passed，18 条既有依赖弃用告警**。测试范围包括整个
`tests/community/core/bot_management`，以及发布/Caller backup、Bot repository、
普通/OpenAPI endpoints、Service API conformance、lifecycle discovery、module
boundaries 和 oversized-module gates。新增 Python 文件 Ruff 检查和
`git diff --check` 均通过。

既有 `BotService` 和两个 router 是仓库已有超长模块，仅增加必要的中立接缝，
未扩充超长文件 allowlist；新实现均拆在小于 1000 行的模块内。Bot repository
原有 997 行，CAS 抽出后降至 967 行；未进行无关的大规模生命周期/路由拆分。

本地修改尚未提交或推送；线上/预发 E2E 尚未运行。

## 2026-10-08 状态查询收敛

备份失败通过已有字段落库。删除 get_bot_status/project_restart_status 与
查询侧任务兜底，两个 status 路由恢复基线。不增加公共回调保护钩子。
仍保留既有本分支的任务依赖传递、后台注册、原子 CAS、重启前状态快照和
provider handoff；后两者服务于持久化重启执行，而非设备回调或状态查询。
