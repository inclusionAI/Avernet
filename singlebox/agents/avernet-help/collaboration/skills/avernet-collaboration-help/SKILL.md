---
name: avernet-collaboration-help
description: 解答 Avernet 自定义协作问题，编写或修改 BCS state_machine YAML，解释校验诊断，并通过固定版本短引用提供只读预览。
allowed-tools:
  - exec
---

# Avernet 自定义协作帮助

咨询直接解释；编写/修改定义执行下述流程；执行、建群或绑定 Bot 按公共 `bcs-coordination` 的独立授权流程处理。

本 Skill 面向已绑定 `bcsPanel` 资源的协作助手，定义 Draft 卡片的交付流程；不要把这套流程推广为其他 Bot 的默认行为。
普通 Bot 使用 Draft 时可直接消费 CLI 结构化结果。本助手生成/修改定义后按下文交付固定版本卡片，替代通用技能的完整 YAML 回复。

编写字段前必须实际读取 [references/authoring-workflow.md](references/authoring-workflow.md) 和
[references/examples.md](references/examples.md)，通过 Runtime 的 Skill 发现结果定位已安装的
`bcs-coordination`，实际读取其 `SKILL.md`、`references/custom-collaboration.md`、
`references/custom-collaboration-schema.md` 和 `references/collaboration-drafts.md`。
不要硬编码仓库或开发机路径。缺少公共 schema 时明确说明，不猜字段。

## 编写与交付

1. 明确逻辑角色、步骤、串并行/Loop、输入和唯一最终交付物。只用逻辑 binding，不把真实 Bot UUID、token 或私密地址写入定义。
2. 每次请求创建独立工作文件，写入 YAML；生成并保存本次 draftId（例如 `uuidgen | tr '[:upper:]' '[:lower:]'`）。
3. 运行 `bcs-cli collaboration draft create "$candidate_file" --draft-id "$draft_id" --title "协作定义"`。
   用于上传原文和校验已存版本；忽略 `create/update` 返回的 `message`，即使已有该字段，也不直接用于卡片交付。
4. 读取短 JSON 的 `phase`、`saved`、`draft_id`、`revision` 和 `validation.validation`。记录已确认保存的 ID 和版本，只有本次固定版 `valid=true`，才能描述为有效定义。
   保存成功但校验无效时保留 draftId/revision，根据真实诊断修改文件，以该版为 expected-revision 调用 update；每次修改必须校验新版。
   最多自动修复两轮；不要为通过校验擅自删除用户要求的 judge、提前退出或其他业务语义。
5. 保存且校验通过后，必须显式运行 `bcs-cli collaboration draft preview "$draft_id" --revision "$revision"` 获取卡片；ID 和版本使用上一步确认的值，不查最新版，也不猜版本号。
   核对这次 preview 的 JSON：只有 `phase=ready`、`validation.validation.valid=true` 且存在非空 `message`，才正常交付。preview 不保存新版本；不要使用旧的离线 `collaboration preview <file>`。
6. 通过当前聊天正常回复本次 `draft preview` 返回的 JSON 的 `message` 字段，可附必要说明；卡片只取自 preview，不能回退到 create/update 的 message。
   原样搬运工具生成的短 AixUI；当前引用参数为 `schemaVersion`、`draftId`、`revision`，不复制 YAML、Base64 或长 JSON，不手写标签或二次转义。
   工具结果不是用户消息；不要只调用工具后说“已生成”。不调用 BCS 消息发送、不寻找私聊 Session、不要求用户先加入群。
7. 修改已有定义先用 `draft get <id> --revision <n> --output <new-file>` 下载指定版，再 `draft update <id> <file> --expected-revision <n>`，随后按第 4–6 步读取保存结果、显式 preview 新的固定版本并交付。
   不改用 head、不自动提高冲突基线、不从聊天文本重建原文。
8. 消息重试重放同一次 preview 的 `message`，不重新 create/update。需重新取得同版消息时用 `draft preview <id> --revision <n>`，仍按第 5 步核对返回结果。
   创建未知复用原 draftId；更新未知只核对 n+1，匹配表示内容已保存，不声称是本次请求创建。
9. 工具/权限/网络失败报告真实阶段。`saved=true` 但后续校验或引用取得失败时，保留该版，通过固定版本 preview 重试后续步骤，不重新建 Draft。已撤销的分享不自动恢复或另存来绕过撤销。

用户明确要求查看无效定义时，使用固定版 `draft preview`，附实际诊断，不描述为有效。
保存成功、校验成功、消息持久化是独立事实；预览不表示执行。

## 地址、身份和分享

复用现有 BCS 地址发现与 Bot 认证，不主动覆盖 localhost/`--url`，不读取或打印 Bot token。
`.bcs/session.json` 是连接配置来源之一，不是必须加入的聊天 Session；已有环境凭据时不强制要求该文件。
只读卡片可转发，持有者可读取该版；撤销使该版所有副本后续读取失败，不追回已下载内容。
共享 Bot 的管理身份不提供不同私聊用户的工具侧隔离。

助手交付只读预览；宿主提供“发起协作”入口时，由用户打开建群浮层、编辑定义、选择自己的 Bot 并确认创建。
