# BCS Provider Bot 属性物理列修复

## 问题

Provider Bot 属性接口将 `user_visibility`、`friend_ext` 和
`friend_check_in_strategy` 读写到 `bcs_bots.bot_info`。实际 `bcs_bots`
表已将三项定义为独立物理列，因此接口回读不能反映物理列中的值。

## 目标

- `PersistentBotRepo` 从三项物理列构造 `BotControlPlaneRecord`。
- `patch_control_plane` 仅在对应字段存在时更新对应物理列；`friend_ext`
  保持整体替换语义。
- `bot_info` 仅保存 Bot 描述信息；本修复不迁移或删除其中已有的旧属性键。

## 边界

- 不修改 Provider 路由、鉴权、应用服务、公开接口或通用 `visibility` 列。
- 不创建或执行 DDL/DML 迁移；目标表列已存在。
- 不纳入当前未跟踪的 Postman collection。

## 验证

1. 持久化仓储测试覆盖三列的单独/组合更新、`friend_ext` 清空与读回。
2. 断言 `bot_info` 不因这三项属性更新而新增对应 JSON 键。
3. 执行 `bcs-bot-store` 的 control-plane conformance tests、应用服务测试与 HTTP Provider 路由测试。
