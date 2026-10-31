> 发布说明（2026-10-31）：本次仅提交 Avernet。默认 Harness 已改为官方内存 TaskStore/QueueManager，取消 H2、跨重启去重/会话恢复要求；下表跨进程证据属于旧实现。合并最新 dev 后的代码与 039 迁移验证单独记录在 pr-validation.json，不能用旧运行证据替代。

# 一期验收核对

更新：2026-10-29。本表用于核对计划要求；未核对项不能按“已有测试文件”推断通过。
2026-10-31：三字段/inline Card 的后续调整已完成专项验收，结果见 `../a2a/minimal-card-validation.json`；
下表旧运行证据不能直接证明该调整已通过。刷新不再保存每 Bot 失败状态。
详细构建、命令和阶段证据见 `implementation-status.md`。跨语言运行证据位于
相邻 `harness-bcs-demo/evidence/`，其中构建哈希标识实际被验证的版本。

| 要求 | 已有验证 | 当前结论 |
| --- | --- | --- |
| 固定 SDK、HTTP+JSON/SSE、Bot 独立路由 | Java SDK Runtime/Controller 测试、真实监听服务 | 已通过主路径；不包含 gRPC/JSON-RPC |
| 流式首 Task、同流 Artifact、独立异步入口 | Rust conformance_a2a_delivery、Java Runtime、旧异步验收 | 已有证据 |
| Card 保真、声明校验及出站策略 | card_contract、conformance_card_fetch、注册/发现/存储契约 | 已有证据 |
| Card 原子注册、手动/周期刷新、CAS 与失败状态 | 注册事务/HTTP、Card 应用/仓储测试、真实 Card 脚本 | 已有证据 |
| 刷新失败后取消原任务 | 真实 HTTP 503 + Workbench 审批等待 + 精确取消 | 已通过；a2a-bcs-card-cancel.json |
| 文本追加、末尾/恢复校准、工具前后不重复 | text/projection/tools 契约、真实工具历史 | 已通过主路径；中途替换明确不支持 |
| 工具扩展声明/协商、重复/非法观察、标准文本隔离 | tools_contract、Java 编码测试、真实模型工具 | 已有证据 |
| exec approve/deny、批量/冲突/越权/过期、接受凭据 | structured_exec/structured_delivery、Java StructuredExec/ApprovalStore、真实 Workbench | 最新 BCS + Java 已重跑通过真实批准/拒绝 |
| 等待暂停预算、关闭 SSE、原 Task/context 恢复 | 预算应用服务 + Memory/SQLite 共享契约、Redis/Tracked 端口契约、真实审批 | 新应用层已通过专项和真实 Ling 批准/拒绝；非多实例租约 |
| 断流重查/订阅、初始提交边界 | BCS HTTP 传输契约；自定义幂等仅兼容显式协商 Server | 默认 Harness 首 Task 前结果未知时不重发；不承诺重启恢复 |
| 完成后跨进程继续会话 | 旧持久化实现的历史证据 | 默认内存版本不支持，不作为本期要求 |
| 心跳、慢消费者、断连、应用关闭 | Java 心跳/慢消费者测试，最新完整 reactor 71 项 | 已通过；已部署新 Jar，中文帧显式 UTF-8 |
| 跟踪失败且取消未确认 | 监听 HTTP + 真 managed queue、首 Task 身份写入失败 SQLite trigger、Memory/SQLite 共享契约 | 已通过专项；精确取消，不伪造已持久化身份/终态，不提前释放执行槽 |
| SQLite/MySQL 完整迁移和故障回滚 | SQLite 全链、独立真实 MySQL 8.4.13 全部 37 迁移 | 已通过存储专项；迁移已冻结 |
| 公共 CI 无 Harness 私有依赖 | Rust 监听 HTTP 测试 Agent | 已有证据 |
| 全部状态/响应变体、取消完成竞争、错误标识 | 最新 A2A 全套 53 项，取消补充 18 项；真实队列/Workbench 初始拒绝验收 | 已通过专项：四种响应、全部状态、重复取消、完成竞争、typed HTTP 原因及结果未知隔离 |
| 真实引擎重试/结构化输出的文本语义 | 原生 Engine + 受控 ModelProvider 重试，真实 Harness 事件和 SDK SSE；另有真实 Ling 验收 | 重复前缀与最终文本一致、中文 UTF-8、非文本结构化输出明确 FAILED 均已通过；任意模型的改写仍按一期限制处理 |
| 原 WS/HTTP Provider 注册、投递、取消 | WS adapter 114 项；15 个相关包 3,573 项通过，32 项环境/既有条件忽略 | 已完成该范围回归；忽略项及另跑 MySQL 的范围见实施记录 |
| 架构门禁、源文件大小、空白检查 | 预算编排已收回真正的应用服务并通过契约；当前 13 个独立门禁已运行 | 7 通过、3 既有失败、3 条件跳过（依赖门禁另含工具跳过）；全仓测试目标编译已通过（605 个可执行目标）；完整门禁仍有既有失败，不宣称全仓门禁通过 |

## 明确延期

- 按用户确认：可信业务用户上下文签名、多实例订阅租约/恢复游标、本期队列之外的并发治理。
- gRPC、push、JSON-RPC、消息替换修订以及新的 Agent → BCS A2A Server/MCP 入口。
- ask_user：按用户 2026-10-29 最新确认，本期不验证、不实现，明确延期；一期只交付 exec 审批。该范围决定不等同于宣称 Harness 缺少所有暂停恢复原语。

## 最终交付状态

- 一期功能和真实模型 E2E 已按最新范围完成；ask_user 不验证、不实现。
- 全仓测试目标编译通过，相关包实际回归及最新专项通过；不宣称全部工作区测试均已执行。
- R25 静态检查完成但失败：77 个既有缺失 trait harness、5 个既有非集中契约入口、79 个既有缺失 impl 入口；逐项核对 HEAD，无新增。当前预算应用和两个上下文驱动的入口匹配通过。
- 两个早期重复的完整架构包装命令已中止（143），不计为通过；独立门禁的既有失败与环境跳过项保留。
- 最新构建与真实批准/拒绝证据哈希一致，机器可读索引见 `validation-results.json`。两仓修改尚未提交或推送。
