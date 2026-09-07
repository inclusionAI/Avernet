---
status: accepted
---

# 空间治理权限与 Skill 授权分离

Space Membership 与 Skill Grant 是两套独立权限事实。Space Administrator 负责空间成员治理，不自动成为空间内所有 Skill 的 Owner 或 Manager；普通空间成员可以查看 Skill 和申请操作权限，但不能直接编辑或执行生命周期命令。

创建 Skill 的用户默认成为该 Skill 唯一的 Skill Owner；一个 Skill 可以有多个 Skill Managers，普通空间成员没有 Skill Grant。Owner 和 Managers 都必须取得 Edit Lease 后才能修改草稿。Owner 离开空间前必须转移所有权；Owner 无法正常交接时，Space Administrator 可通过独立、留痕的紧急接管命令将 Owner 转给当前 Space Member，但不会因此自动获得该 Skill 的日常权限。

`Space.created_by` 只记录创建审计。创建者在创建时成为首位 Space Administrator，但不会获得永久不可撤销的特权。Team Space 始终至少保留一名管理员；存在其他管理员时，创建者可以由其他管理员降级或移除，授权判断只依据当前 Membership。
