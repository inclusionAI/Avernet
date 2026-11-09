# Plan

## Approved design
1. 从 useBotSessionFilesFeature 薄拆共享命令 Hook，保留两种场景各自的文件界面及运行阶段选址。
2. DebugChatComposer 接入 Sender 与 ConversationComposerHost，使用实际 ChatBotView / session。
3. 将 Sender SubmitContext 转换为现有 useBotChat 文件参数；通过现有内部扩展装饰知识引用。
4. 保留调试上传直达入口和模型控件；父组件按完整调试上下文隔离输入状态。

## Boundaries
公共代码只消费既有 composer capability，内部知识实现不迁移。API、协议字段和依赖版本不变。

## Delivery
Avernet dev 功能 PR；内部工程用于本地组装验证；用户于 2026-11-10 追加授权 OCB dev 配套功能 PR，不发布、不变更预发服务配置。

## SDK compatibility finding
真实 Sender 测试发现：SDK 的搜索 effect 只监听 query/open，输入单独 `/` 后进入子菜单时二者均不变，因此不会加载异步列表。共享命令在 root onSelect 时懒加载候选，每次进入刷新；子菜单交由 SDK 本地过滤。采用请求代次守卫丢弃作用域切换/卸载后的旧响应，不修改 SDK 或增加依赖。

## Service editor stage correction
编辑页负责选择调试运行阶段：可编辑服务 Bot 使用 draft；显式 verify 或 prestable 验证保留 verify；只读在线查看保留 online。该阶段同时传给聊天、会话、文件和 composer context，不在内部 knowledge 扩展中全局强改 draft，不更改后端选库或发布逻辑。页面到调试面板的回归必须覆盖已上线 Bot 的 edit/online 路由和无显式阶段的编辑入口。用户于 2026-11-10 重新授权：复审通过后创建 Avernet → dev PR。


## User message presentation (approved 2026-11-10)
公共层新增一个同步、零 I/O 的展示 capability，默认 null；不定义知识库语义，不改变聊天 SDK 或消息存储。两处 user-text 展示调用同一个 Host，assistant 不参与。普通聊天仍保留 Markdown、文件引用及长文折叠；调试聊天保留现有文件名卡片。复制/编辑/重试继续使用原消息，不使用展示投影。内部解析、注册和知识卡片需 OCB 配套交付，不得纳入公共仓库。

```ts
interface UserMessagePresentation { content: string; header: ReactNode; }
type UserMessagePresenter = (content: string) => UserMessagePresentation | null;
// null / unsupported / unrecognized: render the original content unchanged.
getUserMessagePresentation: () => CapabilityResult<UserMessagePresenter | null>;
```

验证：默认/unsupported/null 透传；两个消息面一致；实时与历史同内容同结果；多模态 user 的 text block 不遗漏；assistant 不装饰；特殊字符由 React 安全转义；现有文件与 Markdown/长文测试回归。内部解析完整匹配现有模板的固定头、引用清单和固定尾，异常原样回退；不擅自引入新的消息协议。

## Release integration amendment (2026-11-11)
复用现有两个 Host，不恢复被发布线替换的直传上传实现，不增加知识协议或依赖。
```tsx
// Editor/DebugChatComposer.tsx: extension decorates before file-options conversion
<ConversationComposerHost bot={bot} session={session} command={command} onSend={send}>
  {(composer) => <Sender command={composer.command} slots={{ header: composer.header }}
    placeholder={composer.placeholder} onSubmit={composer.onSend} />}
</ConversationComposerHost>
```
```tsx
// Editor/DebugChatPanel.tsx: one keyed owner for release file feature and composer
<DebugChatInput key={JSON.stringify([botId, ownerId, engineOwnerId, userId, stage, sessionId])} />
// DebugChatInput owns useBotSessionFilesFeature(..., stage) and cancels on unmount.
// User messages reuse DebugUserMessage, not a second inline file renderer.
```
```ts
// Workspace/hooks/useBotSessionFilesFeature.tsx: ordinary callers retain default requests
stageOverride?: BotRuntimeStage;
// result adds cancelUploads, backed by upload.cancelAll
// useBotSessionFileUpload ignores cancelled generations, including late finally/callbacks.
```
替代方案：恢复原 dev 的整套 Composer 会违背发布线选型；直接在面板加 Host 无法覆盖 Sender 的提交上下文及 feature 生命周期，故采用薄的 keyed input 边界。
验证先覆盖 Composer capability 到 Sender/send 的接口及实际 Sender，随后组装内部扩展验证；mock 接口阶段回归不等同真实后端草稿/线上差异验收。
