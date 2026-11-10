---
status: approved
owner: frontend
---
# Bot debug chat slash commands

## Summary
Bot 编辑页调试对话复用普通对话的 slash 输入能力；方案于 2026-11-09 经用户确认实施。

## Acceptance Criteria
- 输入 `/` 可选择 `/file`、`/skill`、`/clear`；平台扩展可注入 `/knowledge`，公共源码不依赖内部服务。
- `/file` 懒加载当前调试会话的已上传文件，引用以资源 ID 发送；上传后可直接发送仅附件消息。
- 复用现有知识库扩展，文件和知识库混合引用不丢失正文、资源引用或知识库标识。
- 请求沿用当前认证用户、Bot owner、知识库源 owner 和真实 runtime stage。
- 切换 Bot、会话、身份或阶段后，旧草稿与引用不进入新会话。
- 保留模型选择、停止生成、Enter 发送 / Shift+Enter 换行；命令选择 Enter 不误发送。
- 普通对话的现有命令行为保持兼容。

## Non-goals
不改后端、数据库、SDK、知识库授权或 Bot 配置；不引入任务、工作流和副屏。

## Validation
先跑相关单元/组件测试，再以最新内部工程组装本地前端连接预发验证；验证通过后创建目标 dev 的 Avernet PR。真实环境证据和缺口记于 validation.md。
