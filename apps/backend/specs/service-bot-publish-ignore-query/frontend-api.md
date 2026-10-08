# 服务 Bot 运维接口：前端对接说明

日期：2026-09-18。范围：修改 ignore rule、查询 rule 列表、原地重启。
三个接口均已在当前 worktree 实现，查询接口为本轮新增。本文不表示这些代码已部署。

## 通用约定

请求发送到当前环境 Backend，复用已有登录认证；前端不直连 Engine，不增加专用密钥。

统一响应：`{success: boolean, message: string, error_code: number, data: object | null}`。
业务失败可能仍为 HTTP 200，必须检查 `success`；参数校验、登录和网关错误也可能直接返回 HTTP 4xx/5xx。不要仅依赖 `error_code === 200`：部分失败结果保留默认 error_code。

| 功能 | 方法 | 路径 |
| --- | --- | --- |
| 增删 rule | POST | `/api/service-bot/publish/ops/publish-ignore` |
| 查询 rule | GET | `/api/service-bot/publish/ops/publish-ignore` |
| 原地重启 | POST | `/api/service-bot/publish/{publish_id}/restart-in-place` |

## 1. 修改 rule

JSON 请求体：

```json
{"bot_id":"20260722_4v17af2c","entity_id":"<主体ID>","stage":"draft","operation":"add","path":"workspace/bin"}
```

| 字段 | 类型/限制 | 含义 |
| --- | --- | --- |
| bot_id | string，1–255，必填 | 服务 Bot ID |
| entity_id | string，1–255，必填 | 主体 ID |
| stage | draft / verify / online，必填 | 当前目标阶段，不指定历史版本 |
| operation | add / remove，必填 | 添加 / 删除一条路径 |
| path | string，1–4096，必填 | 发布复制源根下的精确相对路径 |

不要传 version、request_id 或其他额外字段。管理员可操作全部服务 Bot；普通用户需要已有 ADMIN/OWNER 管理权限，仅可聊天的权限不足。

路径不是 glob：不支持绝对路径、`.` / `..` 路径段、`!` / `#` 前缀、`*?[]`、反斜杠和控制字符；去掉开头 `./` 和末尾 `/`。例如 OpenClaw 根为 `.openclaw` 时，`workspace/bin` 对应其下的目录，而不是填写 `/home/admin/.openclaw/workspace/bin`。目录规则排除整个子树。规则仅影响发布复制，不保证后续其他权限操作跳过该目录。

操作针对当前实例：ARCA 为绑定目标，BaaS 为当前设备快照。添加已有规则、删除不存在规则均为成功的 `unchanged`；不是未来新实例的全局配置。

成功响应示例（摘要占位，实际 revision 为 64 位十六进制 SHA-256）：

```json
{
  "success": true, "message": "OK", "error_code": 200,
  "data": {
    "success": true, "scope": "current_instances", "request_id": "<关联ID>",
    "results": [{"provider":"arca","binding_id":123,"target_id":"<实例ID>","status":"changed","changed":true,"entry_count":1,"revision":"<sha256>"}]
  }
}
```

`status` 为 `changed | unchanged | failed | unknown`。超时可能为 `unknown`，不能认定没有生效。失败项含 `error_code/error_type`，成功项含 `changed/entry_count/revision`。任一失败使整体 success 为 false，但成功实例已经生效，不回滚；应保留逐实例结果并重新查询。

## 2. 查询 rule 列表

```http
GET /api/service-bot/publish/ops/publish-ignore?bot_id=20260722_4v17af2c&entity_id=<URL编码主体ID>&stage=draft
```

仅需 bot_id、entity_id、stage，含义、长度、权限和定位规则同修改接口。用 URLSearchParams 编码，不拼接未转义参数。不传 version、operation 或 path。

```json
{
  "success": true, "message": "OK", "error_code": 200,
  "data": {
    "success": true, "scope": "current_instances", "request_id": "<关联ID>",
    "results": [{"provider":"arca","binding_id":123,"target_id":"<实例ID>","status":"success","paths":["workspace/bin"],"entry_count":1,"revision":"<sha256>"}]
  }
}
```

每个实例独立返回：`status: success | failed | unknown`。成功项 paths 为归一化路径数组，保留文件顺序和重复规则；entry_count 等于数组长度。revision 是文件原始字节摘要，不是版本号，也不是修改接口的并发校验参数。

文件不存在或为空时成功返回 `paths: []`、`entry_count: 0`；读取不会创建文件、锁或 journal。忽略空行与注释，非法规则或不可读文件返回错误，不能渲染为空列表。无当前实例返回 `no_current_instances`，不等同于空规则。

多实例时不要合并路径掩盖差异；以 provider + binding_id + target_id 分组显示。部分成功时保留成功列表，失败实例显示错误和重试入口，不用空数组覆盖此前数据。

## 3. 原地重启

```http
POST /api/service-bot/publish/123/restart-in-place
```

无请求体。publish_id 为应用发布单 ID，不是 bot_id 或 BaaS 发布 ID。沿用原重启接口的登录及发布单协作权限校验。

阶段由发布单推导：VALIDATING/VALIDATE_PUB 对应 verify，SUCCESS/ONLINE_PUB 对应 online；不支持 draft。前端不传 stage、version 或 in_place 参数。

提交成功的 data 包含 `success: true`、`message`、`stage: verify|online`、`bot_uuid`。这只表示异步任务入队，界面应显示“重启已提交”，禁用重复提交，并进入已有进度查询流程。

```json
{
  "success": true, "message": "Restart task submitted, stage: online", "error_code": 200,
  "data": {"success":true,"message":"Restart task submitted, stage: online","stage":"online","bot_uuid":"<目标Bot UUID>"}
}
```

原地重启跳过发布物复制及对应 chown/chmod 流程；配置初始化、独立只读权限处理仍可执行，不等于完全无文件操作。保留原重启调度链路。

启动脚本要求运行目录有 `/home/admin/.service_bot_publish_succeeded` 成功标记。标记表示该运行目录曾正常发布成功，可随持久化 home 保留，不绑定容器 ID。缺少标记会在异步启动阶段失败；旧目录需先走普通发布/重启生成标记，前端不能伪造标记。

### 进度查询（复用已有接口）

`POST /api/service-bot/publish/{publish_id}/restart_status`，无请求体。注意是 POST。

响应外层 data 是发布流程结果，含 `publish_id/status/message/action/data`；内层 `data.data` 是可选的底层进度，存在时其 `status` 为底层状态，如 SUCCESS / FAILED / 处理中状态。外层 `data.status` 是发布单状态，即使保持 SUCCESS，也不代表本次重启完成。

建议间隔 2–3 秒刷新，页面退出停止轮询；等待超时显示“状态未确认”，不自动重复提交重启。队列尚未执行或进度暂不可得时可能没有内层进度，不能据此报成功。

当前提交响应没有唯一重启任务 ID，进度接口可能短暂返回上一次重启结果；前端不能将紧接提交后的旧 SUCCESS 当作本次完成。复用现有任务/操作状态关联逻辑；若无法确认本次操作，应显示状态待确认，而非承诺完成。严格按任务关联需要后续另行扩展接口，本轮不新增字段。

普通重启入口仍为 `POST /api/service-bot/publish/{publish_id}/restart`。失败后可由用户主动选择普通重启，不由前端静默触发。

## 前端交互与错误处理

1. 打开规则面板或切换 stage 时查询，丢弃旧 stage 的迟到响应。
2. 以实例分组展示规则；编辑区新增一条或删除一条，提交期间禁用重复点击。
3. 增删完成后重新 GET 校准展示；unknown 或部分失败也应刷新并保留错误信息。
4. 原地重启按钮明确提示“沿用运行目录，不重新复制发布物”，确认后提交并轮询。

常见 ignore 错误 message：`permission_denied`（无权限）、`stage_not_bound`（阶段未绑定）、`no_current_instances`（无实例）、`binding_conflict`、`unsupported_provider`。单实例调用失败以 results 中 `error_code/error_type` 为准（例如 `engine_rejected`、`engine_call_failed`）；不要依赖错误文本解析 Engine 内部状态。展示安全提示并保留 request_id 便于排查。

前端联调清单：三阶段查询；添加/重复添加；删除/重复删除；缺文件空列表；多实例规则不同；部分失败；无权限；超时未知；原地重启入队与最终状态区分；缺成功标记后的普通重启引导。
