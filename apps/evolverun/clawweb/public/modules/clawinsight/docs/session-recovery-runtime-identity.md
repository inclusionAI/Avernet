# 自愈创建接口：运行身份字段补充

## 接口变化

沿用 `POST /api/insight/v1/internal/monitoring/session-recoveries`。在原请求的 `target` 中增加可选对象 `runtime_identity`，用于定位发生问题的服务 Bot 发布阶段和具体实例。原有字段、响应结构和幂等规则不变。

`runtime_identity` 可以传入完整运行身份 JSON，不要求调用方删减其他字段。自愈侧只提取当前需要的定位字段，未消费的字段不参与定位，也不会因多传而拒绝请求。

个人 Bot（`BOT_TYPE=personal`）按已有 Owner、Bot 和运行绑定定位，不消费服务 Bot 的发布／实例定位字段；`STAGE` 可以为空或不提供。自愈请求和任务不保存这些定位字段，也不自动补成 `online`。服务 Bot 和原有规范化定位对象仍按下表校验。

## 请求示例

```json
{
  "event_id": "evt-example-001",
  "tc_fault_label": null,
  "target": {
    "bot_id": "example-service-bot",
    "entity_id": "example-owner",
    "env": "prod",
    "engine": "OC",
    "runtime_identity": {
      "TOKEN": "dummy",
      "CLIENT_ID": "DEVICE-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "OWNER_ID": "example-owner",
      "BOT_ID": "example-service-bot",
      "ENTITY_ID": "example-owner",
      "ENTITY_TYPE": "staff",
      "BOT_TYPE": "service",
      "AGENT_CODE": "example-service",
      "ADMINS": "",
      "STAGE": "online",
      "VERSION": "V16"
    }
  },
  "session_key": "agent:main:session:original",
  "delivery_key": "evt-example-001-send-1",
  "diagnosis": "本轮任务的诊断描述。"
}
```

## 自愈当前消费的字段

以下字段均位于 `target.runtime_identity` 中。

| 字段 | 要求 | 用途 |
| --- | --- | --- |
| `STAGE` | 服务 Bot 提供 `runtime_identity` 时必填；取 `draft / verify / online`。 | 选择对应阶段的运行绑定，避免把线上问题定位到草稿。 |
| `CLIENT_ID` | 非 TE 服务 Bot 多实例时必填；格式为 `DEVICE-` 加 32 位小写十六进制字符。仅有一个活跃实例时可由自愈服务反查；TE 不要求。 | 锁定发生问题的具体实例，使调查和干预落到同一实例。 |
| `VERSION` | 发布阶段可选，格式如 `V16`；草稿阶段不消费该字段。 | 校验现场发布版本；版本已变化时拒绝继续操作旧现场。 |

也兼容已定义的 `stage / deviceUuid / version` 名称；与对应大写字段同时出现时，值须一致。

原有 `target.bot_id / target.entity_id` 分别使用现场的 `BOT_ID / OWNER_ID`。其他必填字段沿用原接口约定。

`BOT_TYPE=personal` 用于识别不需要服务定位字段的个人 Bot。`runtime_identity` 中的 `TOKEN / OWNER_ID / BOT_ID / ENTITY_ID / ENTITY_TYPE / AGENT_CODE / ADMINS` 及其他扩展字段均允许传入，当前不消费。自愈任务只保存提取后的服务定位字段，实际 Bot 身份和运行绑定仍由既有目标定位流程核实。

`bot_uuid` 和底层命令目标由自愈服务按现有绑定、设备关联查询，无需调用方补充；也不要求提供 `deviceAffinity`。

## 返回约定

- 成功受理仍返回 `202`，响应中的 `task_id` 用于查询任务。
- 字段格式无效返回 `422`；设备不属于该 Bot、设备不可用、缺少唯一实例或发布版本已变化时返回 `409`，不会改选其他实例。
- 旧请求仍可解析；服务 Bot 未提供阶段时按 `online` 定位，多实例未明确设备时拒绝创建。Caller 模式不在本次接入范围。
