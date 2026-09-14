# Desktop Center Skill 前端接入说明

本说明覆盖 OpenClaw/Hermes Desktop 复用既有 Skill 产品 API 的 G1
边界。所有 Bot-scoped 请求都携带 `user_id`；操作共享 Bot 时同时传
`owner_id`。外层 HTTP 200 只表示本次 API 成功，不能替代响应信封、
Reference 状态或 Runtime 状态的判断。

## 读取与参数

前端从市场、Space 或 Bot Skill 列表取得内部十进制 `skill_id` 后，使用：

| 目的 | 方法与路径 |
| --- | --- |
| Bot-facing 详情 | `GET /openapi/v1/bots/{bot_id}/skills/{skill_id}` |
| Published 内容 | `GET /openapi/v1/bots/{bot_id}/skills/{skill_id}/content` |
| Bot 参数 | `GET /openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` |
| 整体替换该 Skill 参数 | `PUT /openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` |
| 无 Bot 的共享 README | `GET /openapi/v1/bots/skills/{skill_id}/readme` |

Center content/README 返回数据库最新 `PUBLISHED` Version 对应的精确
Canonical 内容。读取不会访问 Desktop Engine、不会触发下载，因此设备离线也不应
使其失败；它表示当前期望内容，不表示设备正在运行的实际版本。共享 README 没有
Bot 语义，但仍校验 PUBLIC 或当前用户的 Space membership。

参数 body 保持既有结构：`{"parameters": {"name": value}}`。PUT 替换的是
当前 Skill 名称对应的对象，不得先 GET 全文件再由前端回写；Backend 会保留其它
Skill 和文件根元信息。参数读写仍落到 Bot Engine 的历史
`skill_parameters.json`，不是 Center Version 内容，也不是 observed-runtime 接口。

## Direct desired state

- `POST /openapi/v1/bots/{bot_id}/skills/{skill_id}/activate`
- `POST /openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate`

对当前 Bot 可见的 Center Skill 可以在尚无 Installation 时直接 activate。成功响应
里的 `desired_state` 与 `runtime_projection` 必须分别展示；`PENDING` 或
`DEGRADED` 不能渲染成“设备已生效”。权限顺序是 Bot owner/member 或 App grant，
再校验 PUBLIC/Space 可见性。共享资产只形成 Bot-facing 视图，不会改写其持久 owner。

## 已有内部 Skill 与外部 SC code 不是同一个入口

已有内部 `skill_id` 加入 Set：

```text
PUT /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}
```

用户从 Skill Center 市场只拿到外部 `skill_code` 时，走持久异步 Reference：

```text
POST /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references
Idempotency-Key: <stable command key>

{"skill_codes": ["external-code"]}
```

POST 返回 202 后，使用 collection 或 `/{reference_id}` GET 轮询。每项进入
`COMPLETED` 只表示精确版本已物化并成功加入目标 Set；Runtime 投影结果应从相应
desired-state/runtime 入口判断，不能把 COMPLETED 单独显示为“已在设备生效”。

当前没有 Reference retry API、Desktop 下载/下载进度 API，也没有 Draft 整包替换
API；前端不得虚构按钮或轮询地址。Space Draft、Grant、Lease、Publication、retry、
offline、copy 继续使用各自现有产品入口，G1 不新增 Desktop 专用版本业务。

## 错误与隔离

不存在、跨 owner/Bot、无 App grant、无 Space membership、Offline 或矛盾的
Center 归属均按 not-found 处理，前端不能据此推断其它租户或 Space 的资产存在。
参数文件明确不存在时 GET 返回空对象；拒绝、损坏、读取异常或写入失败是错误，
不能展示为“已清空”或“保存成功”。
