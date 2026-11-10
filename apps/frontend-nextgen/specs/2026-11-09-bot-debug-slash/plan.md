# Plan

## Approved design
1. 从 useBotSessionFilesFeature 薄拆共享命令 Hook，保留两种场景各自的文件界面及运行阶段选址。
2. DebugChatComposer 接入 Sender 与 ConversationComposerHost，使用实际 ChatBotView / session。
3. 将 Sender SubmitContext 转换为现有 useBotChat 文件参数；通过现有内部扩展装饰知识引用。
4. 保留调试上传直达入口和模型控件；父组件按完整调试上下文隔离输入状态。

## Boundaries
公共代码只消费既有 composer capability，内部知识实现不迁移。API、协议字段和依赖版本不变。

## Delivery
Avernet dev 功能 PR；内部工程仅用作本地组装验证，不发布、不变更预发服务配置。

## SDK compatibility finding
真实 Sender 测试发现：SDK 的搜索 effect 只监听 query/open，输入单独 `/` 后进入子菜单时二者均不变，因此不会加载异步列表。共享命令在 root onSelect 时懒加载候选，每次进入刷新；子菜单交由 SDK 本地过滤。采用请求代次守卫丢弃作用域切换/卸载后的旧响应，不修改 SDK 或增加依赖。

## Service editor stage correction
编辑页负责选择调试运行阶段：可编辑服务 Bot 使用 draft；显式 verify 或 prestable 验证保留 verify；只读在线查看保留 online。该阶段同时传给聊天、会话、文件和 composer context，不在内部 knowledge 扩展中全局强改 draft，不更改后端选库或发布逻辑。页面到调试面板的回归必须覆盖已上线 Bot 的 edit/online 路由和无显式阶段的编辑入口。用户于 2026-11-10 重新授权：复审通过后创建 Avernet → dev PR。
