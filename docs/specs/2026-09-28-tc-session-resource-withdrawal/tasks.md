# Tasks: TC Session Resource Withdrawal

## Group 1 — Durable Fact
- [x] T1：先覆盖所有状态、单聊 scope、鉴权、幂等/并发和原子回滚（AC1–4、13）。
- [x] T2：实现 transactional delete + outbox、类型/表/增量 SQL 和仓储协议。

## Group 2 — Delivery
- [x] T3：先覆盖 claim、租约恢复/fencing、重放和积压统计。
- [x] T4：实现仓储 claim/ack/fail/replay；worker 退避/暂停/告警/生命周期（AC5–7、11）。
- [x] T5：先写 HTTP/本地插件消费者契约测试；实现认证投递与严格回执校验。
- [x] T6：严格配置、默认暂停、SecretResolver、生产 fail-closed DI 测试与接线。

## Group 3 — Ready for Review
- [x] T7：正式/旧版 API、删除终态、A/B 独立事件回归（TC 侧 AC3、8–10）；真实 ECB 部分保留明确外部阻塞。
- [x] T8：更新契约、runbook、迁移/回滚/运维工具，运行架构/单测/静态门禁。
- [x] T9：自审、改动行覆盖与文件大小/hygiene 核查，记录结果及 PR 草稿；不擅自提交或推送。

## Verification / Submission Still Open
- [ ] V1：隔离环境重跑完整 Singlebox + artifact verifier（本次失败，详见 validation.md）。
- [ ] V2：按 submission-plan.md 创建两个实际提交/PR 后分别跑 CI、获得独立评审；本次仅生成评审补丁，未 commit/push。

## External Release Gates
- [ ] E1：ECB 冻结草案、持久接收/可信集成鉴权、幂等及删除先到 tombstone。
- [ ] E2：真实联调证明迟到 ready 不恢复、共享引用 A/B 隔离及列表/摘要/检索授权。
- [ ] E3：生产数据库演练、端到端时效、告警阈值及负责人确认后才启用投递。
