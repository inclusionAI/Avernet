
## 2026-09-09：临时 Contract/Document Agent 工作区初始化失败

`agents add` 后恢复注册前快照会删除新生成的上下文文件。空的专用工作区因此被当前 OpenClaw 判定为初始化后异常丢失。Contract 和 Document 生成前统一预置 discovery/workspace_defaults 的中立 Plan 模板，使原快照恢复保留有效上下文。主 Bot 的 Discovery 工作区仍按原样保护，不复制或覆盖主 Bot 内容；不删除 workspace-attestations。相关48项测试通过，包含本机引擎隔离复现及修复。发布时需带上模板目录。

2026-09-15 起最终 Objective/Spec 文档改为由 Renderer 确定性生成，不再创建 Document Agent；本条仅保留为历史事件记录，Contract Agent 的工作区处理仍适用。
