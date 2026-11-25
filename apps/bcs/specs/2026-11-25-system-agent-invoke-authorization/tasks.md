用户已于 2026-11-25 授权实施并创建 PR。本次先交付 reader-first 安全兼容切片；未实现和未验证的整体任务不勾选。OpenSpec artifact complete 不代表所有实现任务完成或获得生产操作许可。当前实施证据见 [implementation.md](implementation.md)。验证断言与最小场景见 [test-plan.md](test-plan.md)。

## 1. 评审与启用前置

- [ ] 1.1 评审 SystemInvoke 命名、单向/非传递范围、target owner / scoped governance 审批矩阵及规则 NULL-only 决策。
- [ ] 1.2 确认可信系统 inventory、绑定/资格 generation 与新鲜度/失效 contract；指定 public local fake 与宿主 provider owners。
- [ ] 1.3 核实目标 tenant/env/store namespace 隔离，无法证明时先设计域键迁移；评审配额、审计留存与 HTTP 暴露面。
- [x] 1.4 重新 fetch 实施时 origin/dev，核对所有已变化的 kind match/消费者/路由，记录基线和当前 test drivers；不对本 spec SHA 的测试冒称新 HEAD。

## 2. 纯契约与安全读端（writer 关闭）

- [x] 2.1 在 bcs-domain 增加 GrantKind/RequestKind::SystemInvoke、稳定 serde 字符串、shape 与代次语义，保持旧四类 round trip。
- [ ] 2.2 定义独立管理与 operation-aware admission application/core 契约、DTO/errors、资格事实 outbound 契约；更新相关 CONTEXT.md 和 boundary/conformance 清单。
- [x] 2.3 扩展现有候选 list/is_authorized 的精确 kind 集，新增 fallible checked read，DB/未知 enum/坏行一律显式失败；Memory/SQL 行为一致。
- [ ] 2.4 审计并隔离通用 admission/build_authz_context 的旧 kinds，禁止新边变成 default-profile；固定新 runtime ref=edge_id、revision=approval generation。
- [x] 2.5 在所有 friend request/list/approve/revoke/通知/writeback 和 owner/manager 读写路径明确 kind 过滤并回归；不开放新 kind 给旧 API。

## 3. Store、事务与治理

- [ ] 3.1 设计 typed 请求快照/approval version/idempotency/audit 存储与原子命令 port，保留旧 migration，不在 message/rules 中塞治理元数据。
- [ ] 3.2 为 SQLite/MySQL 添加独立新 migration，实现申请/决议/授予/撤销事务及稳定锁序，权限/资格/配额 CAS fencing 与 unknown-commit 恢复。
- [ ] 3.3 保持五列唯一键、单 approved owner、身份 byte-exact/no-pad；实现 revoked 后 regrant 代次前进，旧 key replay 不复活。
- [ ] 3.4 实现出入边 keyset page、精确 scoped aggregate/pending count、atomic degree 限额与最小批量展示查询，不新增正向权限缓存。
- [ ] 3.5 用完整 upgrade chain + populated fixtures 运行 SQLite、真实 MySQL；检查拟议复合索引字节预算和 EXPLAIN/rows examined，另列宿主兼容 DB 验收。
- [ ] 3.6 实现资格/身份失效持久 fencing，恢复后必须重新批准；处理已部署缓存/多 worker，证明撤销确认后新调用不能读旧批准。

## 4. Application、HTTP 与 runtime 集成

- [ ] 4.1 实现角色矩阵，禁止新入口 anonymous/auto-claim，identity resolver 校验唯一 scoped bot_uuid；支持受限治理 service，拒绝自报系统资格。
- [ ] 4.2 实现新的 request/grant lifecycle、幂等 receipt、状态冲突/存储错误映射，外部资格读取不持有 DB 锁。
- [ ] 4.3 增加 D6 的独立 HTTP routes/DTO/OpenAPI，逐端点定义验证/响应/错误/鉴权；按评审决定是否暴露 OpenAPI mirror，SDK 同步更新。
- [ ] 4.4 在统一 authz hook 接入 direct invocation 的 originator/operation/资格代次检查，保持一般 admission、friend/public/collaboration 旧语义不受新边扩大。
- [ ] 4.5 composition roots 注入真实/假资格和身份实现、validated limits 与默认关闭 writer，consumer capability 未满足不投递新 kind。

## 5. 分层回归与发布证据

- [ ] 5.1 运行纯类型/shape/策略/身份/状态机单元测试，覆盖所有旧四 kind 与坏输入。
- [ ] 5.2 扩展共享 repo harness，增加管理/运行鉴权 consumer conformance；Memory/SQLite/真实 MySQL drivers 执行同组语义并断言真实 port 命中。
- [ ] 5.3 对每个已注册新 GET/create/request/approve/reject/cancel/revoke/list/degree endpoint 独立执行 HTTP contract；覆盖 401/403/404/400/409/429/503 和 legacy friend/admission/Card 未变。
- [ ] 5.4 用真实数据库事务和 Notify/barrier 确定性验证 approve/cancel、approve/revoke、旧 key/regrant、并发限额及 commit-unknown；不靠 sleep 推断竞态正确性。
- [ ] 5.5 运行 synthetic production-domain singlebox HTTP→application→store→A2A fake-target E2E，证明批准前拒绝、批准后正向成功、反向/委托拒绝、撤销后拒绝与混合 grant 独立性。
- [ ] 5.6 运行受影响 BCS architecture/boundary gates、相关 crates/HTTP/友好与 authority 回归，再按仓库 CI 策略执行扩展门禁；记录分母、失败、跳过和未验证环境。
- [ ] 5.7 先兼容 readers/SDK/runtime receivers，再迁移 DB 后启用 writer；演练关闭/回滚/保留历史，形成 current-SHA CI 与 host 集成记录，未获部署授权不操作生产。

## 当前交付切片与阻塞边界

- 已完成 4/27 整体任务：刷新基线、新类型/纯 shape、安全候选读、旧好友/权限隔离。
- 2.4 已完成旧 admission/AuthzContext 隔离；批准代次到新 runtime context 的投影尚未实现，因此整项不勾选。
- 已跑 domain / Service API / test-support / edge core / SQLite store 与 Memory/SQLite 共享 harness；5.1/5.2 还要求未实现的完整策略/状态机和真实 MySQL，因此整项不勾选。
- 新管理 Service API/HTTP、审批事务/审计/幂等/generation、治理索引/degree、真实 invocation hook 和 E2E **没有实现**；没有用 raw repo upsert 冒充安全审批。
- 生产资格 inventory/代次、域隔离、limits/暴露面仍待指定。此 PR 为 reader-first Draft，不是完整功能或生产启用 PR。
