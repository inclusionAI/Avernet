# Relay 重构修复任务

## 1. Service API 与架构收敛

- [x] 1.1 以 `core/task/task_service_protocol.py` 为唯一 `TaskServiceProtocol` 定义。
- [x] 1.2 `api/task/task_service.py` 改为 re-export，消除 core → api 依赖。
- [x] 1.3 调整 HTTP adapter 辅助文件命名并更新 imports，使架构门禁通过。
- [x] 1.4 更新 API context boundary 的 task intake 依赖。

## 2. Relay Harness 恢复闭环

- [x] 2.1 增加按 runtime profile 选择的恢复策略接口与 registry。
- [x] 2.2 保留现有中心化恢复实现，通过 centralized strategy 委托。
- [x] 2.3 实现 Relay 执行前、执行后规划前、规划后派发前的恢复分支。
- [x] 2.4 实现 Relay BBS 超时释放、重发与阶段恢复。
- [x] 2.5 增加 relay_token 轮换、accepted execution/plan 保留规则测试。

## 3. Relay Trajectory 完整性

- [x] 3.1 `apply_relay_report` 同事务生成 EXECUTE/VERIFY/PLAN/TRANSITION action events。
- [x] 3.2 搜索、派发、恢复、BBS 关键节点补充 trajectory/action log。
- [x] 3.3 新增新协议 Relay trajectory E2E，覆盖单 Bot、多棒、BBS、失败恢复、收口。
- [x] 3.4 确保中心化 trajectory 测试保持通过。

## 4. Dispatcher / Runner 原子语义

- [x] 4.1 将 `dispatch(node)` 固定为单节点原子接口，增加 `dispatch_batch` 包装。
- [x] 4.2 中心化与 Relay 策略都在 Dispatcher 内部调用一次 Runner。
- [x] 4.3 将策略 registry 移至 composition root，禁止入口内硬编码模式分支。
- [x] 4.4 使用 DeliveryIntent 统一 HTTP、首棒和恢复消费者的投递认领。
- [x] 4.5 为 task-loop Skill 增加 node/token 级本地锁与阶段检查点。

## 5. 回归与门禁

- [x] 5.1 完整 task core tests。
- [x] 5.2 HTTP、repository、gateway principal tests。
- [x] 5.3 architecture tests，已修复本变更引入的问题；全量仍有当前分支既存的 feedback、HTTP helper 与 oversized allowlist 门禁失败。
- [x] 5.4 task-loop Skill tests。
- [x] 5.5 `compileall`、`git diff --check`、修改文件行数检查。
