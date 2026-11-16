---
name: todo-runner
description: 执行 Avernet Todo 的标准运行协议。收到 [todo-execute] 或 [todo-resume] 时使用；读取可信运行时注入的 Todo 执行代次、Backend、目标与上报地址，完成工作并把 text、file、url_link 或 code 产物持久化到 Todo。每次运行从 infra 源文件直接加载，不安装到默认 SkillSet，也不创建 workspace/skills 软链接。
metadata:
  version: 1.0.0
  author: avernet-todo
  tags: [todo, task, artifact, execution, openclaw]
---

# todo-runner

本 Skill 负责一次 Todo 执行或返工代次。运行时消息必须以 `[todo-execute]` 或
`[todo-resume]` 开头，并注入本轮可信上下文。每次触发都重新读取本文件；不要把本
Skill 安装到默认 SkillSet，不要在 `workspace/skills` 下创建软链接。

## 进入条件

从运行时消息取得以下字段：

- `todo_id`
- `todo_execution_id`
- `todo_execution_token`
- `backend_url`
- 本轮执行目标或本轮修订要求
- 非文件产物的完整 `POST` 地址
- 文件产物的完整 `POST` 地址

任一标识、凭证、Backend 或目标缺失时停止并明确报告缺失项。不得猜测 Backend，
不得生成或复用其他执行代次的 ID/token。`[todo-resume]` 是新的执行代次，旧代次
立即视为失效。

## 执行流程

1. 理解本轮目标、约束和验收要求，只处理当前 Todo。
2. 完成实际工作；需要读取、分析或生成内容时保留可核验依据。
3. 将用户可消费的结果整理成一种或多种产物。
4. 使用运行时消息给出的地址和 `X-Todo-Execution-Token` 上报产物。
5. 检查 HTTP 状态和响应 Envelope；只有 HTTP 成功且业务 code 成功才算持久化。
6. 最终至少有一个 `is_final_artifact=true` 的产物成功上报后，才能声明本轮完成。

禁止只在聊天中返回结果而不上报产物。上报失败时说明失败，不得声称 Todo 已完成。

## 非文件产物

向运行时消息给出的 report 地址发送 JSON，并在请求头中携带：

```text
X-Todo-Execution-Token: {todo_execution_token}
Content-Type: application/json
```

公共字段：

```json
{
  "todo_execution_id": "{todo_execution_id}",
  "artifact_name": "简短、可读的产物标题，例如：AI 发展预测分析",
  "kind": "text",
  "content": {},
  "is_final_artifact": true
}
```

支持三种非文件类型：

- `text`：`content={"text":"完整文本"}`
- `url_link`：`content={"url":"https://...","type":"可选类型"}`
- `code`：`content` 必须含 `code_base_name` 和 HTTP(S) `code_base_address`，可选
  `pr_link`、`pr_desc`

不得只上传摘要代替完整文本。多个产物分别上报，并正确标记最终产物。

## 文件产物

向运行时消息给出的 object-file 地址发送 `multipart/form-data`：

- `todo_execution_id`
- `artifact_name`（必填）：简短、可读的产物标题，例如「AI 发展预测分析」；不要填文件名，不要用完整正文代替
- `is_final_artifact`
- `file`

同样通过 `X-Todo-Execution-Token` 携带本轮凭证。文件只有对象存储与 Artifact 记录
都成功后才算完成；HTTP 503 或其他失败必须如实报告，不能用一条文本说明伪装成
文件已上传。

## 安全与幂等

- token 只用于运行时指定 Todo 上报接口的请求头。
- 不把 token 写入回复、产物、文件、日志或发送给其他主体。
- 相同内容重试可以依赖服务端去重，但不得改用旧执行代次。
- 收到凭证失效、鉴权失败或状态冲突时立即停止，不自行创建新凭证。
- 不修改 Todo 状态；状态推进由 Backend 根据已持久化事件和执行结果完成。
