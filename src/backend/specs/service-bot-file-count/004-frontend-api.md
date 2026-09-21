# 服务 Bot 目录文件计数：前后端接口

## 请求

`GET /api/service-bot/publish/ops/file-count`

沿用现有登录认证。平台超级管理员和具有目标 Bot ADMIN 权限的用户可调用。接口只读，不修改文件、ignore 规则或发布状态。

| Query 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| bot_id | string | 是 | 服务 Bot ID |
| entity_id | string | 是 | Bot 所属 entity；不是授权凭据 |
| stage | string | 是 | `draft`、`verify`、`online` |
| path | string | 是 | 1–4096 字符，目标目录；不支持 version 参数 |

示例：

```http
GET /api/service-bot/publish/ops/file-count?bot_id=20260918_51d4ual4&entity_id=ENTITY_ID&stage=draft&path=workspace%2Ftest_ignore
```

OpenClaw 中 `workspace/test_ignore` 相对当前引擎根目录；也可传该根内的绝对路径，例如 `/home/admin/.openclaw/workspace/test_ignore`。`.` 查询根目录。不同引擎物理路径可能不同，不能把上述绝对路径当作所有引擎的固定地址。禁止越界路径和符号链接路径。

## 成功结果

```json
{
  "success": true,
  "message": "OK",
  "error_code": 200,
  "data": {
    "success": true,
    "scope": "current_instances",
    "stage": "draft",
    "path": "workspace/test_ignore",
    "request_id": "generated-request-id",
    "results": [
      {
        "provider": "arca",
        "binding_id": 44,
        "instance_id": "instance-id",
        "path": "workspace/test_ignore",
        "status": "success",
        "file_count": 12,
        "elapsed_ms": 35
      }
    ]
  }
}
```

`request_id` 用于问题排查。`elapsed_ms` 为该实例查询耗时。绑定标识的实际类型沿用后端绑定模型，前端只作为标识展示，不做计算。

## 展示规则

- 递归统计普通文件，包含隐藏文件；压缩包算一个文件，不统计包内内容；硬链接按目录项计数。
- 不计目录、符号链接和特殊文件，不沿符号链接进入子目录。空目录成功返回 `0`。
- 统计当前实际运行目录，不是发布物；命中 ignore 的实际文件仍计入。
- draft 查询 Bot 当前草稿运行实例；verify/online 查询当前阶段绑定。没有绑定或实例时明确失败，不回退其他阶段。
- 多实例逐个显示，不能把各副本相加当作 Bot 文件总数。扫描不是原子快照，过程中有文件变化时结果可能变化或失败。
- HTTP 200 不等于业务成功。检查外层 `success`，并逐项检查 `status`。

## 失败与部分成功

实例失败时保留该实例的身份、路径和耗时，`file_count=null`；`status` 为 `failed` 或 `timeout`，另带稳定的 `error_code`。某个实例失败不会抹去其他实例的成功结果，外层 `success=false`。

```json
{
  "provider": "baas",
  "binding_id": 44,
  "instance_id": "replica-b",
  "path": "workspace/test_ignore",
  "status": "timeout",
  "file_count": null,
  "elapsed_ms": 10000,
  "error_code": "scan_timeout"
}
```

| error_code | 前端提示建议 |
| --- | --- |
| invalid_path / path_forbidden | 路径无效或不在允许访问范围内 |
| not_directory / path_not_found | 目标不是目录或目录不存在 |
| permission_denied | 运行实例没有目录读取权限 |
| directory_changed | 扫描期间目录发生变化，可重试 |
| scan_timeout / timeout | 查询超时，可手动重试 |
| busy | 查询繁忙，稍后重试 |
| unsupported | 当前引擎不支持文件计数 |
| engine_call_failed / invalid_engine_response / scan_failed | 查询失败，携带 request_id 联系排查 |

无权限、Bot 不匹配、阶段未绑定等前置失败可能没有 `results`。不要使用 `file_count || 0` 或把失败显示成零；只有 `status=success` 才展示数量。页面可保留成功实例、对失败实例显示“未知”，并提供用户主动重试入口。

本接口需要 Backend 与 Engine 同时具备新协议；旧 Engine 不会降级为全量文件列表查询。

当前支持生产 OpenClaw 文件端口；尚未定义唯一物理根的 Claude Code 和内存测试引擎返回 `unsupported`。每个 Backend 请求最多同时查询 2 个实例，每实例连接/调用截止 30 秒；每个 Engine 进程最多 2 个扫描，扫描截止 10 秒，满载立即返回 `busy`，不排队。
