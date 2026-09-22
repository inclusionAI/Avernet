# 服务 Bot 构建排除规则接口

## 与实例级规则的区别

新增接口管理数据库中的“下一次构建规则”，不需要连接运行容器。旧 `/api/service-bot/publish/ops/publish-ignore` 继续管理指定 stage 的实例文件，语义不变，两者不会自动同步。

规则按 Bot 当前引擎独立保存，服务端确定引擎；前端不传 `engine_type`、`stage` 或 `version`。修改后需重新构建发布物才生效；已构建版本的安装、原地重启和回滚不受影响。构建时固定一次规则快照，后续配置变更不会改变该次构建。

沿用现有登录身份：平台管理员或有该 Bot 管理权限的用户可调用。不能通过提交 `entity_id` 获得其他 Bot 的权限。

## 查询

`GET /api/service-bot/publish/ops/build-ignore?bot_id=<bot_id>&entity_id=<entity_id>`

成功响应示例：

```json
{
  "success": true,
  "data": {
    "scope": "build",
    "engine_type": "openclaw",
    "paths": ["workspace/bin"],
    "revision": 1
  }
}
```

无配置时 `paths=[]`、`revision=0`。读取失败不等于空列表；前端应保留已展示列表并提示错误。

## 添加 / 删除

`POST /api/service-bot/publish/ops/build-ignore`

```json
{
  "bot_id": "<bot_id>",
  "entity_id": "<entity_id>",
  "operation": "add",
  "path": "workspace/bin"
}
```

删除使用 `"operation": "remove"`，其余参数一致。响应包含最新 `paths`、`revision`、`engine_type`、`scope` 及 `changed`。重复添加或删除不存在的规则是成功的幂等操作：`changed=false`，revision 不变。删除最后一条后保留空配置及递增后的 revision。

前端收到成功结果后采用服务端返回列表，不在客户端自行拼接。按钮提交期间禁止重复点击；若发生并发冲突按返回错误提示后重新查询，不假定修改已生效。

## 路径输入

- 相对于该引擎发布物根目录，例如 OpenClaw 的 `workspace/bin` 对应运行时 `/home/admin/.openclaw/workspace/bin`。
- `workspace/bin` 排除该项和整个子树，不排除 `workspace/binary` 或其他目录下的同名项。
- `./workspace/bin/` 规范化为 `workspace/bin`；以响应中的规范化路径显示。
- 本轮不支持绝对路径、通配符、路径穿越或控制字符。请传 `workspace/bin`，不要传 `/home/admin/.openclaw/workspace/bin`。
- 必需的 MCP 配置和 OpenClaw 阶段配置不能排除，包括其祖先目录；例如不能以排除整个 `workspace` 的方式绕过必需配置保护。
- 路径不存在仍可保存，后续构建出现该路径时会排除。规则不是运行容器中的删除指令。

界面建议将本入口命名为“构建排除规则”，说明“下次构建生效”，避免与旧“实例复制排除规则”混淆。

## 错误处理与发布顺序

遵循现有 `ApiResponse` 约定，同时检查 HTTP 状态和业务 `success`；HTTP 200 不代表业务成功。请求格式错误也可能返回 HTTP 422。错误时展示服务端安全业务信息，不把失败转换成空列表或操作成功。

| message | 含义 / 建议处理 |
| --- | --- |
| `permission_denied` | 无 Bot 管理权限，禁用操作 |
| `bot_not_found` / `not_service_bot` | 目标不存在或不是服务 Bot |
| `invalid_ignore_path` | 路径格式不支持，提示修改输入 |
| `required_build_path:<path>` | 与必需配置冲突，展示冲突路径 |
| `ignore_rules_too_large` | 规则总量超限 |
| `concurrent_update` | 并发修改冲突，重新查询后重试 |
| `build_ignore_failed` | 服务或数据库失败，不宣称已保存 |

上线需先创建新表再启用应用代码。旧容器规则不自动导入；需要在新接口显式添加。历史发布记录没有 `ext.publish_ignore` 时保持原有兼容行为。
