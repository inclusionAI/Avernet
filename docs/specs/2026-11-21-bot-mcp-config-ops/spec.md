# Avernet：Bot MCP 配置运维 OpenAPI

状态：草稿，待 review。基于当前 `dev` 的代码和方案讨论。尚未实施。

## 问题与目标

应用拿 App API Key 后，不能使用现有账号级 `config-groups` 给 Bot 批量更新 MCP 配置。这个旧入口要求用户身份，并按用户账号替换分组快照，不适合应用只改某个 Bot 的几个 Header 或 URL。

新增单 Bot、单 MCP 的运维接口。应用拿已有 Bot 授权逐个调用，可限并发，失败按 Bot / MCP 单独重试。不把用户默认配置或其他 Bot 纳入这次写入。

## 接口契约

`GET /openapi/v1/bots/{bot_id}/mcps/{server_code}/config`  
`PUT /openapi/v1/bots/{bot_id}/mcps/{server_code}/config`

- **定位与鉴权**：按现有 Bot 寻址约定解析路径；`user_id` 表示委托人，不替代 Bot 归属。复用 addressed-Bot grant、实时操作权限和编辑锁。GET 按此前建议用 MEMBER，PUT 用显式 ADMIN + EDIT_LOCK。鉴权、存储和下发绑定同一 Bot。App 无授权、授权撤销或权限不足时，不得读写。
- **GET**：只读目标 Bot 的显式覆盖，不返回用户默认值或完整有效配置；不回显凭据型 Header 值。URL 可能包含敏感信息，响应、错误和日志需按已有脱敏规则处理。没有覆盖不等于 MCP 不存在或未安装。
- **PUT**：按字段增量更新，不替换整份配置。仅支持 `headers` 和 `url`；未提供字段或 Header 键保持不变。Header 名按大小写不敏感匹配；同一请求中仅大小写不同的重复键建议拒绝。不接收 `bots[]`、用户默认字段、API Key、环境或协议。
- **清除**：`url: null` 只删除该 Bot 的 URL 覆盖；某 Header 键为 `null` 只删除该键的 Bot 覆盖，已有用户默认值时恢复继承。空字符串不是删除指令。建议 `headers: {}` 表示无变更，不提供一键清空。
- **URL**：沿用旧接口对自定义 HTTP(S) URL 的约定，不要求先登记到 MCP Center，不新增 App 专用的改址白名单或拦截。环境、协议及其他存量字段不因改址而变化。

### 请求示例

仅增加或更新一项 Header，保留已有 URL 和其他 Header：

```json
{"headers":{"X-Region":"cn"}}
```

只改 Bot 的 URL，保留 Header：

```json
{"url":"https://mcp.example.invalid/service"}
```

撤回 Bot URL 和一个 Header 的覆盖，恢复对应的继承：

```json
{"url":null,"headers":{"X-Region":null}}
```

以上是提议中的新接口请求体，不是当前生产接口示例。

## 存储与运行时

1. 重用现有校验和精确 Bot 寻址。事务内锁定目标 Bot、重新读取该 MCP 的覆盖，用当前记录合并本次补丁。写入 `ac_bot_mcp_config`，没有记录且有有效变更才新增；不覆盖未提交字段。与旧账号级入口共用现有的 Bot 写入锁，避免基于过期快照误覆盖其他字段。
2. 保持旧 `config-groups` 和用户级 `/config` 原状。新接口不调用账号快照替换，也不走会改变 Direct 安装状态的入口。**不检查 MCP 是否在 Manifest，不改 Manifest，不改变 SkillSet 或安装关系。** 后续 Manifest Apply 若替换或清理这条覆盖，运维改动可能被覆盖；接口不承诺优先于 Apply。
3. 保存后，如果目标 MCP 当前生效，沿用现有单 Bot 下发；否则只预存，不安装、不下发。运行时沿用同一有效配置解析：URL 按 Bot → 用户默认 → Center；合并显式 Header 时同名由 Bot 覆盖用户默认。运行时文件由原链路生成，不直接编辑 `mcporter.json`。不同引擎可以继续使用自己的投影方式。
4. 响应区分「配置已保存」和运行时「已投影 / 仅预存 / 投影失败」；下发失败不伪装成未保存，投影成功也不承诺 MCP 可连通。重试只作用于同一目标。状态字段的最终命名对齐现有运行时投影契约。

### 改址与显式 Header

按 review 决定，改 URL 不顺手修改 Header，且运行时沿用旧规则。App 获得目标 Bot 的 URL 写权限后，也可能改变显式 Header 的接收方，包括用户手填的 `Authorization`。Center 专用的旧 `api_key`、平台默认 Header 和托管凭据不随自定义 URL 自动下发。这个权限后果应在 PR 和接口文档中明确，不把它误写成「改址一定不改变凭据接收方」。本 spec 不授权使用真实凭据做测试或调用生产。

## 验收边界

建议以 OpenAPI 端到端行为为主，辅以一次现有设备投影或配置生成的回归，确认 URL 真正进入运行时输出，而不只是数据库改了。

1. 有效 App grant + 对应角色可访问；缺失或撤销 grant、权限降级、编辑锁、错误归属、跨租户或同名 Bot 不可误访问。无权请求不写库、不下发。
2. Header-only 保留 URL，URL-only 保留 Header；省略与 `null` 区分，单键删除及大小写规则正确。无目标记录时新建；重试不重复创建。
3. 并发写入不丢掉未修改字段，不影响其他 Bot、用户默认或现有环境/协议。旧账号级接口的既定替换语义不变。
4. 未启用只预存；已启用只向目标 Bot 投影。通过合成 Header、URL 验证有效 URL 及继承行为；下发失败时仍可查询已保存配置。平台托管凭据不跟随自定义 URL。
5. 同一 MCP 出现在 Manifest 时，运维写入仍可执行。后续 Apply 行为保持不变；不会自动改文档或安装。
6. Gateway OpenAPI 与 Backend 行为一致；真实 App 身份的端到端验收在获准的测试环境执行，不在生产试写。

## 不在本次范围

账号级 App 访问、跨 Bot 服务端批量 API、修改 Manifest / SkillSet、自动安装、修改用户默认/API Key/环境/协议、新授权模型、新表、部署或合并。方案评审通过前不发布 issue、不提交代码。

## 依据

- [现有 Config Manage 的 URL 契约](https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/2026-10-09-mcp-scoped-url-rules/spec.md)
- [ADR 0016：URL 与显式 Header 独立](https://github.com/inclusionAI/Avernet/blob/dev/docs/adr/0016-mcp-url-and-explicit-headers-are-independent.md)
- [现有 Bot MCP 路由](https://github.com/inclusionAI/Avernet/blob/dev/apps/backend/src/agentclaw/community/adapters/http/openapi_v1/mcp/router.py)
