---
agent: tc-review
status: completed
created: 2026-09-14
iteration: 4
---
# 应用身份 Caller connection 实现 Spec

## 最新授权模型（用户明确批准）
所有通过现有 Principal 校验且带 app_id 的应用，均可操作同租户中任意用户的已有 Caller 实例。无需应用 grant、应用白名单、owner/public/collaborator 权限校验，不依赖用户认证。user_id 是目标 Caller，允许与身份附带用户及 Bot owner 不同。私有 Bot 的非成员 Caller 同样可访问已有实例。

## 编码边界
- 原 expert_chat/router.py 平放 POST /api/v1/expert-chats/app-caller-connection；bot_id/owner_id/user_id 必填，force_upgrade 默认false；ApiResponse/need_poll/connection保持原约定。
- BaaS直连并透传X-Avernet-Principal。require_app_caller复用ordinary verifier，必须带app_id，verify_audience=False，其余现有签名/issuer/exp/主体结构校验不变。
- 仅新精确路径在DI及仓储访问前以已验证Principal tenant建立上下文，并在请求退出恢复。service再核对tenant一致。
- service检查当前tenant中Bot存在，然后复用get_authorized_caller_connection(operator_id=user_id,is_super_admin=False)：必须已有实例且ext含非空bot_uuid；不允许首次创建，不赋管理员权限，复用已有force_upgrade/轮询行为。
- 删除本任务引入且已无用途的grant/collaborator依赖、导入、fixture；不修改其他grant/协作者功能。
- 保留安全结构化日志及递归凭据脱敏；所有认证应用可跨用户访问的权限范围须在Protocol/README明确。
- 无Gateway/BaaS/Engine/Relay/schema更改。仅本任务测试/manifest和文档同步。

## 验证
1. 实签APP或APP+USER、无Cookie通过；USER-only/无效/缺失Principal 401；BaaS audience沿用现有策略。
2. 不存在grant的不同应用都能访问私有Bot的非成员Caller已有实例；不查grant/collaborator；Bot不存在/跨tenant/缺实例/无有效bot_uuid拒绝且无生命周期副作用。
3. 真实框架DI/仓储用例无grant种子仍成功；原用户接口回归不变。
4. live Singlebox：admin实际provision私有Bot的非owner Caller实例，再由APP-only无grant复用相同实例及UUID，缺实例仍拒绝。manifest登记新路由，既有阈值不降低。
5. 保留日志成功/失败/敏感值不落日志、DI tenant及验签一次断言。完整live/CI结果以实际job为准。

## 交付
在现有feature worktree修改；本次本地commit后交主agent rebase/push/PR验证，不部署、不合并。
