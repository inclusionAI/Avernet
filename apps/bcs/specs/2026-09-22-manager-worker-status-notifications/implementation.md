# 实施记录

日期：2026-09-22

## 实现范围

- 新增显式 `UserNotification`，复用现有公开历史和前端通知流程，不生成 Bot delivery。
  原 `GenericNotification` 空 receivers 的广播语义不变。
- 任务汇总、任务目标不存在和协同操作未确认提示改为用户-only；Worker 主动消息和正常结果不变。
- Ledger 增加 `Cancelled` / `cancelled` 分类，历史 summary 缺字段时默认空列表。
- 原派发 intent 和最多 80 个 Unicode 字符的任务摘要进入现有 task 元数据。
  无 intent 的旧任务使用 task_id；不查询 MCP 缓存、不扩展 MCP 回执。
- Worker error/aborted 回调使用短 TaskResult。确认失败/取消但没有回调时，
  复用 managed delivery 的终态 CAS + reply admission 事务生成结果。
  同一个 `task-result:{task_id}` 身份抑制回调、控制路径和重放的重复结果。
- `Unknown` / `CancelUnknown` 不产生异常结果；传输异常但没有明确拒绝时不假定失败。
- 用户主动停止的原因随既有控制元数据保留。结果投递前仍检查 Session、群与参与者权限；
  已结束 Session/已关闭群不能被结果 Send 唤醒。
- legacy 明确拒绝、停止确认复用原终态处理入口；失败/中断结果投递异常不会把 Worker 改回执行中。
  普通成功结果保留已有 legacy 投递/重试语义。

## 事务与成本

不新增扫描、表、数据库迁移、轮询或通知框架。managed 终态仍使用原先的所有权读取、
锁内状态读取、上下文查询与 `commit_transition`；额外工作是内存解析 task 元数据、
按既有顺序同时取得 Worker/Manager 锁，并在原事务中写入至多一条结果消息和一条结果 delivery。
结果队列容量、TTL、幂等、网络结果不明不重发均沿用原逻辑。没有事务内网络 I/O。
legacy 结果额外读取一次指定 Session 检查停止状态，不扫描历史或其他任务。

错误可见历史与生命周期展示仍为既有 post-commit 投影，存储失败会返回错误；
用户-only ledger 提示除外：其保存/发布错误只记录服务端日志，不改变 Manager 回执。
不新增 durable inbox/outbox 或承诺进程崩溃后自动补齐所有展示。

## 兼容性

不改外部 MCP 工具、请求参数或回执，不改数据库 schema，不新增前端组件。
新增元数据字段在新版读取时支持旧记录缺省。旧二进制中的严格 JSON 解码器不一定接受
新版 task/transport 字段，因此不能承诺直接回滚旧二进制读取尚未结算的新版任务；
回滚应保留兼容读取代码，或先结算新版任务。结果消息与 ledger 的生产者/消费者一起发布。

## 验证

验证覆盖：用户-only 历史/实时展示且零 Bot delivery、普通广播兼容、正常成功、
失败/中断短文本、无回调显式停止（managed 与 legacy）、排队取消、Unknown/CancelUnknown、
回调与控制竞争、重复回放、同 Worker 多 intent、重启恢复元数据、旧元数据兼容、
Manager 投递失败不重做 Worker、关闭群禁止唤醒。

按用户本轮明确要求，不拆分原有文件。`contract_bot_event.rs` 保持原结构，
仅更新与本需求冲突的断言；原有超过 1,000 行的问题不在本次调整范围。

实际验证结果：

- `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-message-flow -p bcs-system-message -p bcs-domain -p bcs-service-api`：773 项通过，0 失败。
- 增加旧 ledger summary 兼容性断言后，再运行 `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-domain`：61 项通过，0 失败（与上一项包含重复测试，不累加）。
- `git diff --check`：通过。
- `cargo test --manifest-path src/bcs/Cargo.toml --workspace`：未通过，停止于 `e2e_ws_messaging::test_group_create_and_list` 的本地 HTTP `/groups` 请求超时。同一测试单独使用 `-- --exact` 重跑仍超时，约 122 秒；未通过干净基线对照证明其成因，本次未修改此测试或宣称全量回归通过。
- `bash src/bcs/scripts/ci/arch-check.sh`：已报告失败，包括未修改的 `check-deps.sh:36` 未绑定变量、导入/trait 命名检查及 R25 契约登记问题；运行超过 13 分钟后，在 R25 测试枚举阶段停止，未获得完整总报告，不能宣称通过。未修改或放宽检查规则。

日志保留在本机 `/tmp/bcs-status-final-tests.log`、`/tmp/bcs-status-domain-tests.log`、
`/tmp/bcs-status-workspace-tests.log`、`/tmp/bcs-status-e2e-retry.log` 和
`/tmp/bcs-status-architecture.log`，不纳入仓库。

未执行部署、提交或推送；尚未运行独立 Singlebox 覆盖率门禁。

## 用户展示错误隔离补充（2026-09-22）

`emit_task_ledger_status` 在展示边界记录通知错误，所有派发与终态调用点不再传播该错误。
任务入队、task.assigned 事件及 TaskResult 持久化/投递的错误语义保持不变。
不处理已延期的 Manager 通知失败收尾和迟到回调缓存清理，不拆文件。

新增回归用例模拟用户提示持久化失败，同时覆盖 queued 和 legacy dispatched 路径：
仍返回原任务标识与正确派发状态，不额外生成 delivery，后续 Worker Error 仍正常生成并
发送 TaskResult。`cargo test --manifest-path src/bcs/Cargo.toml -p bcs-message-flow -p bcs-system-message`
通过（531 passed，0 failed）；`git diff --check` 通过。未重跑全 workspace 或 Singlebox。
