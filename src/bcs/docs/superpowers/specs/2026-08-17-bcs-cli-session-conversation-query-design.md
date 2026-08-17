# bcs-cli 会话反查钉钉 Conversation ID 设计

- 日期：2026-08-17
- 状态：已实现
- 范围：Channel 会话映射管理查询与 bcs-cli 诊断命令

## 背景

BCS 已持久化 `bcs_session_id` 到 IM conversation 的映射，出站投递也通过该映射定位
钉钉会话，但操作人员无法从 bcs-cli 直接反查。排查 channel 投递时只能绕过服务边界
查询存储，既不方便，也容易忽略运行环境和 binding 的 channel 类型。

## 决策

1. 扩展 `ChannelService`，提供按 `bcs_session_id` 查询 conversation mapping 的应用层能力，
   并支持可选 `channel_type` 过滤。
2. 复用 `ConversationSessionRepoPort::list_by_bcs_session`；不新增 SQL。数据库实现继续使用
   参数绑定，应用服务通过 binding 识别 channel 类型，不向 CLI 暴露 provider 配置或
   IM 用户 ID。
3. 新增人类身份保护的管理接口：
   `GET /channels/conversations/by-session?bcs_session_id=...&channel_type=dingtalk`。
4. 新增 `bcs-cli channel conversation-id --session <session_id>`。CLI 固定查询
   `dingtalk`，普通输出展示 conversation ID、binding ID 和 session scope；`--json`
   返回完整 `items`。
5. 响应使用列表而不是单值。同一 BCS session 可能存在多个 binding 或 per-sender 映射，
   调用方不能假设 conversation ID 唯一。

## Contract 传播范围

- Service API：`ChannelService::list_conversations_by_session`，additive change。
- HTTP delivery contract：新增管理查询路由与 `ConversationListResponse`。
- 消费方：bcs-cli 的 `BcsClient` 和 `channel conversation-id` 命令。
- 实现方：`BcsChannelService`、disabled/noop/test implementations。
- 持久化、配置和数据库 schema：无变化。

## 验收标准

- 缺少合法人类身份时返回未授权。
- 空白 `bcs_session_id` 返回参数错误。
- CLI 查询只返回 `dingtalk` binding 对应的映射。
- 同一 session 的多条映射全部返回且顺序沿用 repository 的稳定顺序。
- session ID 通过 HTTP query 编码传输，不拼接到 URL 或 SQL。

## 用法

```bash
bcs-cli channel conversation-id --session 'group_1:session_1'
bcs-cli --json channel conversation-id --session 'group_1:session_1'
```
