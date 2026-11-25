# AICoding 个人 Bot 异步回收与备份

状态：已实现；2026-11-16 rebase 到 dev 并对齐当前重启备份策略。本文不代表部署或真实容器恢复验收通过。

## 1. 范围与分层

**2026-11-24 修正：只有确认快盘的 `aicoding` / `claude_code` 进入异步回收队列。非 coding、非快盘 coding、快盘探测失败/未知均走原同步回收，不创建 queue/journal 状态；只有异步快盘回收需要状态查询。备份策略仍仅由 AICoding 拥有。**

通用接口保留个人 managed-cloud、OWNER 权限、Teclaw 排除、owner 别名与幂等校验。
与重启一样，通用调用方只选择引擎策略，不解释快盘配置、备份预算或 provider 状态。

| 层 | 责任 |
| --- | --- |
| HTTP / `bot_dormant` | 按快盘准入结果选择同步回收或单个 worker；异步路径复用查询与激活 |
| `engines/provisioning.py` / `restart_contract.py` | 中立备份钩子、共享生命周期互斥及既有 operation 状态/契约 |
| `engines/aicoding/recycle_strategy.py` | 仅拥有 coding 备份配置、探测、预算、凭据与阻断策略 |
| `core/bot_dormant/recycle_task.py` / `recycle_service.py` | durable handler、operation 阶段、目标验证和窄 provider 适配；搬移而非复制 |
| repository / composition roots | 原事务落库；`BotDormantModule` 组装统一 worker，宿主 ARCA 绑定地址不变 |

没有新增通用生命周期平台、表/DDL、MQ、历史任务列表、取消/人工确认 API，恢复也没有迁移到新队列。

## 2. 与重启的复用关系

```text
POST recycle
  -> RecycleBotService.submit
  -> 同一持久任务 -> HTTP 202
       worker -> strategy.prepare_recycle
                   default: no-op，不探测/不读开关/不执行 helper
                   coding: 既有 prepare_restart
                             -> 快盘探测；非快盘跳过 helper 与开关
                             -> 已确认快盘：既有配置 / helper / receipt 校验
```

`prepare_recycle` 保存在 AICoding mixin，直接复用 `prepare_restart`，不是复制一套备份规则。
`fastdisk_fail_block` 仍由原 `_fastdisk_backup_blocking` 在确认快盘后惰性读取；每个需要备份的 operation 保存首次读取结果。
复用当前 dev 的 `FASTDISK_TIMEOUT=1500`、普通备份 1500 秒预算及 partial-success 语义。
非快盘或初次探测失败跳过 helper；最终成功凭据只校验一次，不在释放前重复查询 helper。目标/绑定检查保持不变。
保存稳定 operation ID、首次开始时间、已确认快盘和 warning，重投不会重置预算或丢弃证据。

备份上下文只携带备份证据，不设置 `current_restart`，因此不会触发重启 mutation fence 或更新重启状态。
共享 `DeviceService` / Router / BaaS、Local、ARCA provider 类不新增回收方法。
`ArcaRecycleTransport` 与重启的 `ArcaRestartTransport` 相同模式：组合根注入原始 SDK 操作，策略拥有释放/观察政策。
SDK 适配仍留在 provider 层，不把网络调用细节搬进通用业务代码。

## 3. HTTP 契约

### 提交

`POST /openapi/v1/bots/{bot_id}/recycle`，无 Body。
`entity_id` 解析 Owner；deprecated `owner_id` 保留，两者冲突 422。找不到或无权限均 404。

| 场景 | 结果 |
| --- | --- |
| 任一支持的个人 Bot 新任务 | 202，`operation_id`、`phase=QUEUED`、`changed=true` |
| 任一支持的个人 Bot 正在回收 | 202，同一 handle，`changed=false` |
| 已回收，无未解决操作 | 200，`changed=false`，不重复备份、销毁或审计 |
| 状态冲突 / 生命周期忙 | 409 |
| worker 不可用 / 持久准入不能确认 | 503 |

确认快盘的 coding Bot 返回 202，仅表示任务已持久化，不表示资源已释放；释放错误在 operation 中查询。非 coding、非快盘 coding 和探测未知沿用同步 200，成功响应即表示原回收逻辑完成，不产生 operation 查询状态。历史扫描/ops 的内部同步 `recycle()` 不改为队列。

### 独立查询

`GET /openapi/v1/bots/{bot_id}/recycle-operations/{operation_id}`，不复用 Bot `/status`。
同样要求 OWNER；校验 Bot、owner、tenant、env、app 和 operation ID。GET 严格只读。
异步快盘路径使用同一记录与查询；同步路径没有 operation，不应发起查询。没有匹配的异步 operation 时返回 404。

公开字段为 `bot_id`、`owner_id`、`operation_id`、`bot_status`、`phase`、`error_code`、`message`、安全 `warning`。
任务 FAILED 仍是成功读取（HTTP 200）。只支持当前/最近一次 operation；被后续 operation 替换后旧 ID 返回 404。
不返回原始 ext、任务 payload、凭据或 SDK 异常。恢复不清空最后一次回收结果。

## 4. 回收任务与失败语义

复用 `ac_task_queue` 与 `Bot.ext.recycle`，没有新增表。
队列 payload 保存不可变作用域/目标快照，业务阶段只以 ext 日志为准。
先持久化队列，再条件初始化日志；worker 能修复 enqueue 后退出的窗口。
日志合并与 finish 使用已有真实事务、Bot 行锁和 operation/phase 检查。

```text
QUEUED -> BACKING_UP -> RELEASING -> SUCCEEDED
              |              |
            FAILED         FAILED
```

- 阻断开启时备份失败/超时：operation FAILED，Bot 不改成 FAILED，保留原绑定和 Passport，不写回收成功审计。脚本本身的暂停/归档行为沿用原重启实现，不承诺业务进程零影响。
- 阻断关闭时：沿原备份策略 best effort，降级保留 warning。目标变化不能降级放行。
- 只有赢得 RELEASING 条件转换的执行者可以提交释放；同一任务不重复调用释放。
- 备份之后沿用原回收成功判定：释放调用成功就进入收尾，不新增物理状态轮询，也不改 BaaS。
- 释放/收尾失败记为 FAILED，不主动改变 Bot 状态；这不代表物理资源的副作用已回滚。
- 新任务不进入 NEEDS_CONFIRMATION；历史记录仍可查询并沿用已有保护，不自动重放或改库。

释放调用成功后，Passport freeze 保留 best-effort；freeze 结果不明不盲目接管执行权。
事务一次性写绑定 RELEASED、Bot RECYCLED、operation SUCCEEDED 与 `source=openapi` 审计（run_id=operation ID）。
事务失败只补收尾；重复投递不重复成功审计。此路径不 enqueue recycle 钉钉通知。

释放/收尾的并发执行等待预算 600 秒，间隔 3 秒；不查询物理销毁状态，队列 deadline 24 小时。
这些是 worker 预算，不是 HTTP 超时，也不能硬取消正在进行的 SDK 调用或容器脚本。

## 5. 恢复与生命周期互斥

共享服务通过引擎策略钩子检查冲突，不直接解读 AICoding 的 ext 节点。
三类共用既有回收 journal 和互斥，不因引擎不同绕过未结束/待确认操作；仅备份由 AICoding 策略拥有。
重启准入仍用原 coding_restart CAS，同时拒绝已被回收占用的目标。

恢复接口 URL、返回形状、后台启动链路不变，不新增恢复状态机或查询。
RECYCLED -> REACTIVATING 由事务领取执行权；线程启动失败撤回尚未分配的 claim。
回滚不能冻结或覆盖已经分配的新绑定；历史 RECYCLED 仍可恢复。

## 6. 验证与发布

覆盖确认快盘 coding 的异步 202、非 coding/非快盘 coding 的同步 200、异步查询/激活、非 coding 不探测备份、非快盘 coding 不读备份开关，以及配置/预算/上下文复用、物理目标变化、响应丢失、并发重投、事务回滚及审计去重。
结构测试禁止 `bot_dormant` 直接 import AICoding 实现，也禁止向共享 DeviceService 增加 recycle 专用方法。
宿主 ARCA 测试通过组合根绑定的 transport 消费真实 SandboxRuntimeClient seam，不依赖新增 provider 方法。

发布时先保证所有 worker 都认识 `bot.recycle`，再打开回收异步提交；必要时滚动升级期间在入口暂时关闭 recycle POST。
客户端按返回的 200/202 处理；只有拿到 operation handle 才轮询。
预发仍需验证真实容器的备份、销毁与正常启动链路的数据恢复。本地自动化通过不能替代该验收。

## 7. 2026-11-16 文件收敛

以下为历史收敛记录；异步适用范围与文件归属已由第 8 节的确认方案替代。

保持行为和测试断言不变，删除仅为本需求拆出的 4 个小源码模块与 3 个测试模块：
备份上下文并入既有 AICoding `restart_state.py`；释放适配并入 `recycle_runtime.py`；
中立依赖类型并入既有 `restart_contract.py`；中立策略分发装饰器并入既有 `provisioning.py`。
对应测试并入已有 schema、回收 runtime 和回收策略测试，不删除覆盖。
快盘探测、配置读取、备份预算、阻断及 warning 仍全部由 AICoding 策略拥有；
默认引擎不创建队列、探测快盘或新增锁。接口、持久化和并发保护不变。

## 8. 2026-11-17 统一契约（替代此前默认引擎同步策略）

历史方案曾统一三类异步回收；2026-11-24 已修正为仅确认快盘 coding 异步，其余走原同步回收。复用原任务表、状态和发布顺序；没有增加第二条任务管线。
原 coding worker 搬到 `core/bot_dormant/recycle_task.py`；物理释放 runtime 并入既有 `recycle_service.py`；
状态/窄能力契约并入既有 `restart_contract.py`。ARCA transport 的宿主导入地址保持不变。
`recycle_strategy.py` 仅保留 coding 备份，通用 worker 不依赖 coding 备份异常或实现。

## 9. 2026-11-24 同步/异步准入修复

`RecycleOperations.submit()` 先完成已有的运行中操作、重复任务、RECYCLED 幂等、生命周期和重启冲突检查，再调用引擎策略的检测-only 准入钩子：

- 默认策略直接返回同步；不探测快盘、不读 `fastdisk_fail_block`。
- AICoding 复用重启策略的 live fastdisk detector；只确认快盘才入队。
- 探测失败或未知按非快盘兼容路径同步回收。
- 已有 `QUEUED` / `BACKING_UP` / `RELEASING` / `NEEDS_CONFIRMATION` 仍优先返回原 operation，不被同步分流绕过。

同步回收返回 `operation_id=null`、`phase=null`；OpenAPI 保留成功审计，internal manual 保留成功审计与通知。
