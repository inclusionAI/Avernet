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
- 请求沿用当前认证用户、Bot owner、知识库源 owner 和真实 runtime stage；服务 Bot 编辑调试使用 draft，显式/生命周期验证保留 verify，只读在线查看保留 online。
- 切换 Bot、会话、身份或阶段后，旧草稿与引用不进入新会话。
- 保留模型选择、停止生成、Enter 发送 / Shift+Enter 换行；命令选择 Enter 不误发送。
- 普通对话的现有命令行为保持兼容。

- 用户消息展示允许部署侧提供同步、纯展示的引用卡片投影；普通聊天与编辑调试一致，公共默认完全透传。发送正文、历史原文、复制和编辑来源保持不变。
- 平台模板识别和知识库卡片实现归内部工程，不引入公共源码；不能识别的消息完整回退。

## Non-goals
不改后端、数据库、SDK、知识库授权或 Bot 配置；不引入任务、工作流和副屏。

## Validation
先跑相关单元/组件测试，再以最新内部工程组装本地前端连接预发验证；验证通过后创建目标 dev 的 Avernet PR。真实环境证据和缺口记于 validation.md。

## Release integration amendment (2026-11-11, approved continuation)
以发布回流 PR #3847 的 d14a5cee6928edf2296aeb4508a2d26cab267e73 为开发基线，不等待合入 dev；不修改该 PR、不推送或新建 PR。
- 保留发布线 Sender、共享文件 feature、上传 Modal 和资源面板；知识库仍由部署扩展提供。
- 当扩展启用时，调试输入的命令、菜单、选中引用与发送装饰同时生效；文件和知识库混发保留原文件协议。
- 调试用户消息使用已有展示 Host；文件名卡片和模型/停止按钮不退化。
- 切换完整调试上下文时清除输入、引用、弹层和上传；取消后晚到的上传不得写回新会话。


## Latest dev delivery amendment (2026-11-11, approved)
The user authorized resuming both repository changes from current dev, local paired validation and two dev-targeted PRs. This supersedes the earlier release-head/no-PR iteration constraint; preserve that earlier evidence as history. Public baseline: 3958a48287907da0393cd38992e8e62c42f4703d, including the merged release integration. Retain the release Sender/file feature; mount only the existing generic composer and user-message presentation extension points. Service debug file requests use the explicit resolved stage; personal debug file requests retain the stage-omitted shape. Knowledge request/card protocol ownership remains in the companion enterprise implementation.

Tracking issue: https://github.com/inclusionAI/Avernet/issues/3866.
