---
status: accepted
---

# Team Skill Draft 使用独立持久化 Edit Lease 与 fencing

团队空间 Skill Draft 的并发编辑使用独立持久化 Edit Lease，而不直接复用服务 Bot 的永久协作锁，也不使用分布式 Cache 作为锁实现或权威来源。新增专用协作锁表 `ac_skill_draft_edit_lease`，租约记录、持有人、过期时间和单调递增的 fencing token 全部持久化在数据库中；Skill Owner 和 Skill Managers 的所有 Draft 写入都在最终落库事务中校验当前 holder、token 和有效期，从而保证被抢占页面的迟到请求不能覆盖新持有人的内容。

现有 `ac_bot_collab_lock` 缺少 TTL、租约代次和落库 fencing，复用会把不同资源键与权限语义耦合，并无法兑现产品“抢占后旧编辑者不能继续保存”的合同。现有 Cache 锁不能与 Draft 更新处于同一个数据库事务，也不具备单调递增的 fencing token，因此本期不将其放入正确性链路；未来即使增加 Cache，也只能作为可删除的性能优化，不能改变数据库为唯一事实来源的合同。个人空间 Skill 不使用该租约，仍通过 Draft revision 的乐观并发校验防止多页面丢失更新。

协作锁表至少保存 `skill_draft_id`、`env`、`holder_user_id`、`fencing_token`、`expires_at`、`created_at` 和 `updated_at`，并以 `UNIQUE(skill_draft_id, env)` 保证一个 Team Skill Draft 在同一环境下最多只有一个当前租约记录。获取、过期重获和抢占必须在数据库事务内锁定该行并递增 fencing token；续租和释放必须同时匹配当前 holder 与 token，禁止旧请求续租或释放新持有人的租约。
