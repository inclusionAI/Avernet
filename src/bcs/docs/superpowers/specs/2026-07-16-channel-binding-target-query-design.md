# Channel Binding 按目标查询设计

- 日期：2026-07-16
- 更新：2026-07-23（ChannelBinding repository 增加运行环境隔离）
- 状态：已完成
- 范围：Channel binding 管理查询接口

## 背景

现有 `GET /channels/bindings` 返回全部绑定，适合管理 CLI，但 Bot 和群管理页面只需要当前目标的绑定。前端拉取全量数据后再过滤会扩大不必要的数据暴露范围，也让页面结果依赖客户端过滤。

## 决策

1. 保留现有全量查询接口及响应结构，兼容 CLI 和既有管理调用方；查询结果限定为当前运行环境。
2. 新增 `GET /channels/bindings/by-target`，必须指定 `target_type=bot|group` 与非空 `target_id`，可选 `channel_type`。
3. HTTP adapter 将查询参数转换为领域 `BindingTarget`，application service 调用 repository 的目标查询能力，并沿用 provider 配置脱敏。
4. repository 由 composition root 绑定到当前运行环境，并在存储层按环境与目标筛选；数据库实现使用参数化查询，内存实现遵循相同语义。
5. 第一版沿用现有 binding 管理接口的人类身份认证边界，不在本次改造中新增 Bot owner 或群成员授权模型。

## 验收标准

- 当请求缺少合法的人类身份时，按目标查询应该返回未授权。
- 当 `target_id` 为空白时，按目标查询应该返回参数错误。
- 当指定 Bot 目标时，响应只包含该 Bot 的绑定。
- 当指定 Group 目标时，响应只包含该 Group 的绑定。
- 当额外指定 `channel_type` 时，响应只包含该渠道类型的目标绑定。
- 当不同环境存在相同目标或渠道账号时，响应、入站查询和管理写操作只能访问当前运行环境的绑定。
- 当返回 provider 配置时，敏感字段应该保持脱敏。
- 当旧调用方继续请求 `GET /channels/bindings` 时，响应结构保持不变，结果只包含当前运行环境的绑定。

## 接口

```http
GET /channels/bindings/by-target?target_type=bot&target_id=bot_1%3Auser_1&channel_type=dingtalk
GET /channels/bindings/by-target?target_type=group&target_id=group_1&channel_type=dingtalk
```

响应继续使用现有 `BindingListResponse`。

## 测试

- repository contract：运行环境隔离、Bot/Group 目标隔离与可选渠道过滤。
- service：目标查询结果的 provider 配置脱敏。
- HTTP contract：新路由的人类身份认证。
- route unit：目标类型转换与空目标校验。
- e2e story：登录用户按 Bot target 查询绑定并获得稳定的 `items` 响应。
