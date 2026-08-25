# BCS Provider Bot 属性物理列修复：编码报告

## 既有链路

Provider 属性 HTTP 路由通过应用服务调用 `BotControlPlaneRepoPort`，其持久化实现为
`PersistentBotRepo`。此前该实现把三项 Provider 属性嵌入 `bcs_bots.bot_info` JSON。

## 允许新增点

- 在 `PersistentBotRepo` 的 control-plane 查询投影中选择三项物理列。
- 在 `control_plane_record_from_row` 中解析三项物理列。
- 在 `patch_control_plane` 中对已提供字段作参数化列更新。
- 在持久化仓储 conformance 测试中验证物理列与 `bot_info` 的边界。

## 禁止触碰点

- Provider 属性路由、单 Token 鉴权和应用服务。
- 普通 `visibility` 字段、数据库迁移和公开接口。
- 当前工作区未跟踪的 Postman collection。

## 实现

- `user_visibility` 和 `friend_check_in_strategy` 以已序列化的枚举字符串写入各自的
  `VARCHAR` 列。
- `friend_ext` 以 JSON 对象整体替换写入 `friend_ext` 列，传空对象仍保留清空语义。
- 读取时，缺失/NULL 的列回退到领域默认值；不合法的已持久化值会记录不含字段值的
  warning 并返回内部错误，避免静默错误数据。
- 描述信息仍由 `bot_info` JSON 更新；属性 PATCH 不再写入三项 JSON key。

## 诊断

复用既有的持久化属性 PATCH debug 日志，并为损坏的持久化属性增加列名级别 warning；
不记录 token、Bot 属性内容或其他敏感值。
