# AICoding 个人 Bot 异步回收与备份

状态：已实现，2026-11-08 按重启备份策略收敛职责；本轮调整未提交、未推送。未部署，未完成真实容器恢复验收。

## 1. 范围与分层

**仅 AICoding（`aicoding` / `claude_code`）进入新增异步备份回收。其他引擎保留原同步回收，不入队、不探测快盘、不增加 provider 释放轮询。**

通用接口保留个人 managed-cloud、OWNER 权限、Teclaw 排除、owner 别名与幂等校验。
与重启一样，通用调用方只选择引擎策略，不解释快盘配置、备份预算或 provider 状态。

| 层 | 责任 |
| --- | --- |
| HTTP / `bot_dormant` | 提交与查询入口、权限、响应封装、选择策略；默认同步路径继续原审计 |
| `engines/provisioning.py` | 中立的 `execute_recycle`、查询与生命周期钩子；默认不创建异步资源 |
| `engines/aicoding/recycle_strategy.py` | `AicodingRecycleMixin`：异步准入、备份、冲突策略 |
| `engines/aicoding/recycle_task/state/runtime/release.py` | durable handler、operation 阶段、目标验证和窄 provider 适配 |
| repository / composition roots | 事务落库；`AICodingModule` 组装 worker，宿主绑定 ARCA 原始能力 |

没有新增通用生命周期平台、表/DDL、MQ、历史任务列表、取消/人工确认 API，恢复也没有迁移到新队列。

## 2. 与重启的复用关系

```text
POST recycle
  -> RecycleBotService.submit
  -> strategy.execute_recycle
       default: 原同步 recycle -> HTTP 200
       AICoding: 持久任务 -> HTTP 202
                   worker -> strategy.prepare_recycle
                              -> 既有 prepare_restart
                              -> 既有备份配置 / helper / receipt 校验
```

`prepare_recycle` 保存在 AICoding mixin，直接复用 `prepare_restart`，不是复制一套备份规则。
`fastdisk_fail_block` 仍由原 `_fastdisk_backup_blocking` 读取；每个 operation 保存首次读取结果。
复用既有 `FASTDISK_TIMEOUT=300`、普通备份 1500 秒预算及 partial-success 语义。
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
| AICoding 新任务 | 202，`operation_id`、`phase=QUEUED`、`changed=true` |
| AICoding 正在回收 | 202，同一 handle，`changed=false` |
| 默认引擎成功回收 | 保留 200，`status=RECYCLED`、`changed=true`，不创建 operation |
| 已回收，无未解决操作 | 200，`changed=false`，不重复备份、销毁或审计 |
| 状态冲突 / 不可受理 / 队列不可用 | 409；AICoding 准入不能确认时 503 |

202 仅表示新任务已持久化，不表示资源已经释放。默认引擎仍使用原同步错误语义（例如 release failure 502）。

### 独立查询

`GET /openapi/v1/bots/{bot_id}/recycle-operations/{operation_id}`，不复用 Bot `/status`。
同样要求 OWNER；校验 Bot、owner、tenant、env、app 和 operation ID。GET 严格只读。
默认引擎不创建该记录，查询为 404，也不会因此构造 AICoding runtime。

公开字段为 `bot_id`、`owner_id`、`operation_id`、`bot_status`、`phase`、`error_code`、`message`、安全 `warning`。
任务 FAILED 仍是成功读取（HTTP 200）。只支持当前/最近一次 operation；被后续 operation 替换后旧 ID 返回 404。
不返回原始 ext、任务 payload、凭据或 SDK 异常。恢复不清空最后一次回收结果。

## 4. AICoding 任务与失败语义

复用 `ac_task_queue` 与 `Bot.ext.recycle`，没有新增表。
队列 payload 保存不可变作用域/目标快照，业务阶段只以 ext 日志为准。
先持久化队列，再条件初始化日志；worker 能修复 enqueue 后退出的窗口。
日志合并与 finish 使用已有真实事务、Bot 行锁和 operation/phase 检查。

```text
QUEUED -> BACKING_UP -> RELEASING -> SUCCEEDED
              |              |
            FAILED    NEEDS_CONFIRMATION
```

- 阻断开启时备份失败/超时：operation FAILED，Bot 不改成 FAILED，保留原绑定和 Passport，不写回收成功审计。脚本本身的暂停/归档行为沿用原重启实现，不承诺业务进程零影响。
- 阻断关闭时：沿原备份策略 best effort，降级保留 warning。目标变化不能降级放行。
- 只有赢得 RELEASING 条件转换的执行者可以首次提交 destroy；响应丢失只观察，不盲目重发。
- BaaS 的逻辑状态、受理、任意 404 或空列表都不能单独证明物理释放；ARCA 必须明确证明 captured sandbox 已销毁。
- 释放/收尾不明时保留占用并进入 NEEDS_CONFIRMATION，不让新恢复、重启、删除、停止绕过。

释放确认后，Passport freeze 保留 best-effort；freeze 结果不明不盲目接管执行权。
事务一次性写绑定 RELEASED、Bot RECYCLED、operation SUCCEEDED 与 `source=openapi` 审计（run_id=operation ID）。
事务失败只补收尾；重复投递不重复成功审计。此路径不 enqueue recycle 钉钉通知。

释放/收尾观察预算 600 秒，间隔 3 秒；队列 deadline 24 小时。
这些是 worker 预算，不是 HTTP 超时，也不能硬取消正在进行的 SDK 调用或容器脚本。

## 5. 恢复与生命周期互斥

共享服务通过引擎策略钩子检查冲突，不直接解读 AICoding 的 ext 节点。
AICoding 负责未结束/待确认回收的 veto；默认策略不新增 AICoding 锁或日志检查。
重启准入仍用原 coding_restart CAS，同时拒绝已被回收占用的目标。

恢复接口 URL、返回形状、后台启动链路不变，不新增恢复状态机或查询。
RECYCLED -> REACTIVATING 由事务领取执行权；线程启动失败撤回尚未分配的 claim。
回滚不能冻结或覆盖已经分配的新绑定；历史 RECYCLED 仍可恢复。

## 6. 验证与发布

覆盖默认引擎不入队/同步 200、AICoding 策略异步 202、配置/预算/上下文复用、物理目标变化、响应丢失、并发重投、事务回滚及审计去重。
结构测试禁止 `bot_dormant` 直接 import AICoding 实现，也禁止向共享 DeviceService 增加 recycle 专用方法。
宿主 ARCA 测试通过组合根绑定的 transport 消费真实 SandboxRuntimeClient seam，不依赖新增 provider 方法。

发布时先保证所有 worker 都认识 `bot.recycle`，再打开 AICoding 异步提交；必要时滚动升级期间在入口暂时关闭 recycle POST。
客户端按引擎返回的 200/202 处理；只有拿到 operation handle 才轮询。
预发仍需验证真实容器的备份、销毁与正常启动链路的数据恢复。本地自动化通过不能替代该验收。
