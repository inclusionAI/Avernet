# 指定 Bot 许可证上线运维接口

## Summary

新增 Bearer Token 保护的内部运维接口：

```http
POST /api/internal/dormant/unfreeze-passport-one
Authorization: Bearer <DORMANT_INTERNAL_TOKEN>
Content-Type: application/json

{
  "bot_id": "default",
  "owner_id": "37565",
  "reason": "recover license after service bot restart_publish_for_others"
}
```

该接口只调用 `PassportPlugin.unfreeze_agent_passport` 将指定 Bot 的身份凭据上线。
它不读取或修改 Bot 状态，不启动容器，也不复用完整的 `ActivateBotService` 流程。

## Motivation

Bot 可能已经处于 `ACTIVE`，但许可证因异常回收仍被冻结。完整 activate 流程会修改
状态并启动容器，不适合这种仅订正许可证状态的场景。运维需要一个影响面受限、可审计、
同步返回 Passport 调用结果的内部入口。

## Scope

### In Scope

- 在现有 `/api/internal/dormant` Router 下新增接口。
- 复用现有 `verify_dormant_internal_token` Bearer Token 鉴权。
- 请求体包含非空的 `bot_id`、`owner_id`、`reason`。
- Core 运维服务调用 `PassportPlugin.unfreeze_agent_passport`。
- 记录请求、成功和失败日志；日志包含 Bot、Owner 和审计 reason。
- 对请求转发、成功响应、Passport 异常和输入校验增加测试。

### Out of Scope

- 查询或校验 `BotModel` 是否存在、是否为 `ACTIVE`。
- 修改 `BotModel.status`。
- 启动或重启 Bot 容器。
- 调用 `ActivateBotService.activate` 或完整 activate 状态机。
- 新增数据库审计表；`reason` 通过结构化日志和 Passport SDK 请求进入审计链路。
- 修改 `PassportPlugin` 合同或具体插件实现。

## Architecture

HTTP Adapter 只负责鉴权、请求解析和异常到 HTTP 状态码的映射。业务动作位于
`DormantOpsService`：它依赖既有的 `PassportPlugin` 合同，不依赖具体 Passport 实现。
这保持 `core -> plugin_api` 的依赖方向，并避免在 Router 中直接选择插件实现。

调用链：

```text
HTTP request
  -> verify_dormant_internal_token
  -> unfreeze_passport_one router handler
  -> DormantOpsService.unfreeze_passport_one
  -> PassportPlugin.unfreeze_agent_passport
  -> configured Passport implementation
```

`DormantOpsService` 继续作为小型运维编排入口。其构造函数新增 `PassportPlugin` 依赖，
现有 dormant recycle 行为保持不变。

## API Contract

### Request

`OpsUnfreezePassportOneRequest`：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `bot_id` | string | 去除首尾空白后长度至少 1 | Bot ID |
| `owner_id` | string | 去除首尾空白后长度至少 1 | Owner 工号 |
| `reason` | string | 去除首尾空白后长度至少 1 | Passport 审计原因 |

### Success

HTTP 200：

```json
{
  "ok": true,
  "data": {
    "bot_id": "default",
    "owner_id": "37565",
    "status": "passport_online"
  }
}
```

`PassportPlugin.unfreeze_agent_passport` 是同步调用。只有它未抛异常时才返回成功。

### Errors

- Bearer Token 缺失或错误：复用现有鉴权行为。
- 请求字段为空或仅包含空白：HTTP 422，由 Pydantic 请求模型拒绝。
- Passport 调用抛出异常：记录异常日志并返回 HTTP 500。

## Observability and Audit

Router 在收到请求时记录 `bot_id`、`owner_id`、`reason`。Core Service 在 Passport 调用
开始、成功和失败时记录相同审计维度。不得记录 Bearer Token 或 Passport 凭据。

## Testing

采用测试驱动开发：

1. Router 测试先证明新路径不存在或未转发，然后实现路由。
2. Service 测试证明参数被原样传给 `PassportPlugin.unfreeze_agent_passport`。
3. Service 测试证明 Passport 异常向上传播，Router 映射为 HTTP 500。
4. 请求模型测试覆盖空白字段返回 HTTP 422。
5. 测试不绑定 `ActivateBotService`，并断言该接口只调用 Passport 运维服务。
6. 运行 dormant 模块测试、Ruff 和仓库后端 CI 门禁。

## Acceptance Criteria

- [ ] 使用正确 Bearer Token 和有效请求时，只调用一次 Passport 解冻能力。
- [ ] `reason` 不被替换或丢弃。
- [ ] 成功响应明确标识 `passport_online`。
- [ ] Passport 异常不会被误报为成功。
- [ ] 空白 `bot_id`、`owner_id` 或 `reason` 被拒绝。
- [ ] 不读写 Bot 状态，不启动容器，不调用完整 activate 流程。
- [ ] 现有 dormant 接口与测试保持通过。

## Rollout

代码首先合入 `inclusionAI/Avernet:dev`。合并提交同步到内部
`code.alipay.com/mirrors/Avernet` 后，OCB 再将 `ocb-public` gitlink 更新到该提交。
外层 OCB 不应提前固定到仅存在于未合并 PR 分支的提交。
