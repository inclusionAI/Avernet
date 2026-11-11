# JSON 转 A2A 1.0 独立工具

## 归属与基线

归属 `tools/a2a-json-tool`，从 Avernet `dev` 的 `3a16cf9eca52226dde6ba1071fc02338fa6fcf2f` 引入。该目录提供开发调试工具，不属于产品前端、BCS 服务或共享 A2A SDK；没有修改现有 Service API、Plugin API、数据库或运行时依赖，也不需要新增系统 ADR。

需求记录：[Avernet #3905](https://github.com/inclusionAI/Avernet/issues/3905)。实现 PR 通过该记录关联，合并状态以 GitHub 为准。

固定依据为 [A2A v1.0.0 规范](https://a2a-protocol.org/v1.0.0/specification/)和 [a2a.proto](https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto)。当前使用方式和字段映射由[工具 README](../../README.md)维护。

## 问题与范围

调试 A2A 时，需要从简化 Agent 配置、业务 JSON、消息字段片段或对象打印文本生成可检查的协议 JSON，并知道源字段对应到哪里。本工具提供网页、CLI 和可复用函数，无第三方运行时依赖。

1. AgentCard 模式将简化 `url` 转为 `supportedInterfaces`，也接受完整卡片；检查名称、版本、接口、技能及其非空 `tags`，不虚构必需业务数据。
2. 消息模式将一般业务 JSON 完整放入 `params.message.parts[0].data`；识别结构化消息时，将 `content` 转为 `text`，按明确业务约定将根/包装内外层 `sessionId` 转为 `params.message.contextId`。多个来源或显式 contextId 冲突时拒绝转换，其余业务字段保留到 metadata。
3. 支持缺外层括号的字段片段、`key=value` 与 `Message(...)`/`TextPart(...)`；只作可解释的有限修复，不执行文本或猜测缺失字段值。
4. 网页默认消息请求页，提供 4 个消息 Demo 和 3 个 AgentCard Demo、导入、格式化、复制下载、解析预览、映射表，以及双击字段名/值后的输出高亮；切换 tab 保留各自草稿。
5. 服务只监听回环地址并提供白名单静态文件，输入仅在浏览器处理。CLI 的 stdout 只输出 JSON，说明和错误使用 stderr，失败返回非零状态。

## 实现与接入

- 纯 ESM 转换、解析及路径关联逻辑由网页与 CLI 共用；DOM 操作留在 `web/app.js`，Node 静态服务留在 `server.mjs`。
- 从本地原型整理源码、测试和使用说明；历史截图、用户输入、机器路径与逐轮会话记录不属于提交内容。
- 通过根 README 和 CONTEXT-MAP 提供导航；专属工作流只对本工具及自身变更运行 Node 22 测试，不接入产品构建流程。
- 接口地址示例使用回环地址，其他协议测试使用保留示例域名；不包含生产端点或凭据。

## 验收与限制

验收依据见[任务](tasks.md)和[验证记录](validation.md)。核心字段测试、片段解析、metadata 保真、冲突拒绝、字段定位、7 个 Demo、CLI 与 HTTP 测试必须通过。

本工具不是完整协议认证器，不连接远端 Agent，不检查远端认证、能力、任务归属或是否接受客户端 contextId。复杂可选字段的检查边界会在输出说明中列明；旧版字段及已签名卡片不自动迁移。JavaScript 不安全整数拒绝转换，精确大整数应使用字符串。

此规格记录实现和验证，不代表已合并、部署或完成远端互操作验证。PR 与后续检查状态由关联 GitHub 记录维护。
