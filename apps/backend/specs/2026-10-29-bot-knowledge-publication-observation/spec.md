# Spec: 服务 Bot 分状态知识库选择（2026-11-02 修订）

## Summary
草稿、预发、线上使用各自的知识库开关状态；复用管理和会话查询 URL，不新增表，不写 Bot / publication ext。历史快照只作辅助配置，失败不改变原生构建、发布、回滚结果。

## Approved behavior
- AC-01: 草稿读写现有 enabled，保留 Editor、space、tenant、CAS 和原生草稿可编辑性检查。
- AC-02: BUILDING 独立保存该 publication 最近成功采样；[] 是明确全关，缺失/损坏/读取异常是未知；不从草稿补齐。
- AC-03: 预发按原生当前预发 publication 读快照；线上按原生当前线上 publication 及工作流标识读 online_enabled，未知不是 false。
- AC-04: 原生上线/回滚实际部署成功后，在既有 progress task 外围尽力投影快照至 online_enabled；不加入原生事务，不改 enabled/revision/updated_by，不新增任务或重试。
- AC-05: 回滚恢复目标版本，不清理历史快照，不回写草稿；无历史记录时保留存储，对外返回正常空页，不提示未知或泄露底层错误。
- AC-06: available-knowledge-bases 和 conversation-knowledge-bases 共用分状态读；后者保留原生会话授权，不能借快照绕过当前 grant / ready 检查。
- AC-07: 新增 runtime_stage 参数；历史个人 Bot 请求保持原响应，服务 Bot 有效状态返回 selection_status、publication_id、captured_at；未知状态仅返回 {items: [], next_cursor: null}；不保留新增的独立 publication HTTP URL。
- AC-08: 每页不超过20，历史采样不超过100；不遍历版本历史、不逐知识库远程调用。线上投影每次只处理前后快照的有界 ID 集合。
- AC-09: 无新 DDL、无历史回填；仅映射数据库已存在的 online_enabled。无记录的历史服务 Bot 不被误报成全关。
- AC-10: 前端管理和会话携带 runtime_stage，非草稿只读，未知正常展示空列表，不展示未知提示或底层报错；不影响原有 AGENTS.md 与消息发送逻辑。

## Non-goals and limitations
无强一致运行时配置、无新 worker/ledger/outbox、无新授权体系。采样是同一 publication 最近成功记录，不证明某次构建的不可变内容。捕获异常不等于硬耗时隔离；共享 DB 故障和驱动超时仍可能影响整个系统。真实设备和生产部署须另外验收。
