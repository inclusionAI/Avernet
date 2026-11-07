# 从协作预览发起协作

## 需求

同步 [#3082](https://github.com/inclusionAI/Avernet/pull/3082)、
[#3136](https://github.com/inclusionAI/Avernet/pull/3136)、
[#3161](https://github.com/inclusionAI/Avernet/pull/3161) 的前端能力到 nextgen。
副屏通过 `onOpenCreateGroup({ draft })` 唤起建群，使用登录用户的自有 Bot 发起协作。

## 行为

- 接受 schemaVersion 2 的 draftId/revision；读取固定版本并核对 UTF-8 字节数及 SHA-256。
- 自动校验并展示流程预览、角色绑定；保留 YAML 编辑入口，固定自定义协作类型。
- 发起 Bot 来自 nextgen 的身份加载流程（OpenAPI bots/mine）。优先当前自有 Bot，只有一个时自动选中。
- 切换发起 Bot 后，保留 YAML、群名和目标，重新查询候选、校验并清空角色绑定。
- 协作目标、必填角色、运行能力校验通过才可提交。提交前重新校验当前 YAML，并固定提交快照。
- 请求期间禁止重复提交；建群结果未知时保留表单和提示，关闭本次提交入口。
- 提交预检超时为 15 秒，建群超时为 60 秒；超时后的建群结果按未知处理。
- 成功后使用提交时的 driver 身份整页跳转到返回的群及初始会话，重新加载身份、群列表与会话上下文。
- 重复唤起同一 Draft 保留表单；切换副屏或关闭副屏时释放未提交的弹窗。

## 接口

| 用途 | 契约 |
| --- | --- |
| 自有身份 | `GET /openapi/v1/collaboration/bots/mine` |
| 协作候选 | `GET /openapi/v1/collaboration/bots/{botId}/candidates?purpose=collaboration` |
| 建群 | `POST /openapi/v1/collaboration/groups`，`collaboration.definition.content_yaml` 与 `participant_bindings` |
| Draft 读取 | `GET /api/v1/collaboration/drafts/{draftId}/revisions/{revision}` |
| 当前 YAML 校验 | `POST /api/v1/collaboration/definitions/validate` |

Draft 和定义校验使用已发布 V1 facade；建群、身份和候选使用 OpenAPI。
OpenAPI 建群负责创建初始会话和启动状态机；定义需要 human 输入时遵循服务端的延迟启动行为。
表单复用 nextgen 的基础字段、角色绑定、Bot 选择与流程预览组件，普通建群入口继续使用原有流程。
