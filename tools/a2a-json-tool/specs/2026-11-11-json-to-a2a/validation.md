# 验证记录

## 本地自动化

2026-11-11，macOS、Node.js v26.4.0，在整理后的工具目录执行：

```bash
node --test --test-reporter=tap
```

结果为 84 项全部通过：原有 76 项解析/转换/字段定位/CLI/HTTP 测试，新增 1 项 AgentCard 非空 tags 回归和 7 项 Demo 检查。空 tags 回归在修复前以“缺少预期异常”失败，启用已有非空数组校验后通过。

HTTP 测试需要本机回环端口。首次在禁止监听的沙箱中运行时，72 项通过、4 项因 `listen EPERM` 失败；允许回环监听后原始 76 项及整理后的完整 84 项均通过。该环境限制没有通过跳过测试处理。

## 页面验证

整理后的副本以独立端口启动，浏览器确认默认消息页、Demo 区域和映射表正常。结构化消息生成 `SendMessage`，contextId 与示例 sessionId 一致；AgentCard 输出接口协议版本 `1.0`。将技能 tags 改为 `[]` 后显示准确字段错误，清空输出并禁用复制，控制台没有错误或警告。

此前同一网页代码已验证 7 个 Demo 的逐个载入转换、Enter 载入、tab 草稿保留、消息标识重置，以及 1280px/390px 布局；迁移没有修改网页交互代码。本次未重复做全部响应式和复制下载检查。所有浏览器测试使用合成示例。

## 验证边界与后续调整

初版曾配置 Node 22 专属工作流，并在 PR #3906 的提交 `bd2835e6c` 上通过。2026-11-11 按交付范围调整移除该工作流；当前方案在工具目录执行 `npm test`，不再为本工具新增 GitHub Actions 检查。此前测试结果作为历史验证保留，不代表后续提交已自动运行工具测试。

此次调整只删除新增工作流并同步文档，未修改转换器、网页或测试代码，因此没有重复运行 84 项测试。没有执行与本工具无关的产品模块或 Singlebox 测试，也未向远程 Agent 发送请求。

移除后检查：相对 dev 的 `.github/workflows` 差异为空，工具文档相对链接有效，没有残留的已删除工作流链接，`git diff --check` 通过。

19 个新增源码文件最大 293 行，全部低于 1,000 行限制；JavaScript 语法检查、工具 Markdown 相对链接和本机/私有数据标记检查通过。仓库推送门禁保持启用，实际推送与远端检查结果记录在 PR 中，不以本文宣称合并或部署。

## CLI 命令交付（2026-11-12）

在基础脚本上增加 npm `bin` 入口 `a2a-json`、可执行 shebang 和帮助，仍共用 `convertInput`。4 条帮助回归在旧实现退出 1，新实现均退出 0；测试保持标准输入管道打开，验证帮助不会等待读取输入。加上原转换与错误场景，CLI 共 8/8 通过，完整套件为 88/88 通过（本地 Node v26.4.0）。

使用临时 npm 用户配置将全局前缀和缓存指向临时目录，以离线方式执行 `npm link`。随后从另一个工作目录调用安装后的命令，验证帮助、含空格的相对文件、标准输入片段、sessionId 映射、AgentCard 与错误退出。验证后清理临时前缀，没有修改用户原全局 npm 安装。未测试 Windows 的命令 shim。

README 补充注册、文件/管道调用、直接 Node 调用和解除链接方式；未改网页或转换逻辑，没有增加模型调用或 GitHub 工作流。

## CLI 命令更名验证

2026-11-12：完成 12.1，将命令注册、帮助、错误提示和当前使用说明中的 CLI 名称改为 `a2a`，保留 npm 包名和工具目录。4 条帮助回归在旧名称下失败，改名后 CLI 8/8 通过；本地原工具和 PR 副本均验证通过。

使用临时 npm 前缀离线安装，从另一目录验证 `a2a --help`、含空格的相对文件、管道消息片段与 AgentCard 转换；全新安装仅注册 `a2a`。模拟旧版安装后重新 `npm link`，新命令可用，但 npm 保留旧 `a2a-json` 链接，已在 README 说明。临时安装已清理，未修改用户全局 npm 安装。

语法、改动源文件行数与 diff 空白检查通过。本轮仅修改命令名及相关说明，运行最接近的 CLI 测试与安装验证；此前完整 88 项和网页验证保留为历史结果，本轮未重复执行，也未验证 Windows 命令包装器。

## PR 评论修复验证

2026-11-12：核对 PR #3906 的 3 条行内评论，审查 head 为 `7cb5bdb7de24426762b6666790f2f0bb97e9b321`、base 为 `9a61798cb7752308aaa01f8e59d78a46809066cb`；刷新 merge ref `dbef0ffa326f28e38cc6dda538bf2fa9e6f68460` 并核对双亲，其工具目录与 head 一致。审查范围为评论涉及的解析、消息映射与字段联动链路。

| 评论 | 原问题及修复 | 验证 |
| --- | --- | --- |
| [4539579844](https://github.com/inclusionAI/Avernet/pull/3906#discussion_r4539579844) | 外层业务 parts 阻止嵌套消息转换；分派、提取和字段联动现共用消息选择规则 | 带外层 parts 的消息经 CLI 成功提取 text，外层数据留在 sourceEnvelope；根消息优先与字段名/值联动回归通过 |
| [4539724403](https://github.com/inclusionAI/Avernet/pull/3906#discussion_r4539724403) | https:// 被误当有效 URI；增加解析与结构检查 | 非法 Part.url 经 CLI 非零退出且 stdout 为空；非法扩展整组留在 metadata；有效 HTTPS、URN、文件 URI 与字段联动回归通过 |
| [4540342975](https://github.com/inclusionAI/Avernet/pull/3906#discussion_r4540342975) | 对象打印中的 urn:example:ext 被误拒绝；解析器支持一般 scheme: 值 | CLI 对象打印成功保留标准 extensions；URN 等值及字段区间保真，缺逗号/空白歧义继续拒绝 |

新增 7 项回归：消息选择/URI 相关 4 项在旧实现失败后通过，根消息优先通过；解析器 URN 相关回归先失败后通过，边界拒绝回归通过。完成 14.1、14.2、14.3 后，完整 `node --test --test-reporter=tap` 为 **95/95 通过**（Node v26.4.0），覆盖所有 Demo、CLI、HTTP 和字段联动。测试仅保留 PATH/TMPDIR 环境变量，使用临时目录和临时回环端口；4 个实际 CLI 场景通过。工具源码没有第三方运行时依赖或外部网络调用。

本轮没有执行实际浏览器双击交互，字段名/值选择与输出路径由回归测试验证；未验证 URI 资源可访问性、远端 Agent 或完整协议认证。原 4173 页面及用户输入未操作。没有新增 GitHub 工作流、改动仓库最外层 README 或调用大模型。

## URI 非法字符追加修复

2026-11-12：完成 15.1，核对评论 [4540755869](https://github.com/inclusionAI/Avernet/pull/3906#discussion_r4540755869)。审查 head 为 `fb2a0a0d0d8c257a0e31514326c1529a5727daf9`、base 为 `9a61798cb7752308aaa01f8e59d78a46809066cb`；merge ref `cc0da875a35d62adf634b0d8a2b8cc9c3afea3dd` 双亲与之对应，工具目录与 head 一致。

根因：`new URL()` 会接受或规范化部分非法原文；例如 `urn:example:<ext>` 会作为扩展输出。现在先依据 [RFC 3986 Appendix A](https://www.rfc-editor.org/rfc/rfc3986.html#appendix-A) 检查 URI 原文的 authority、path、query、fragment 字符及 `%HH` 编码，再保留原有地址解析与非空检查。非法文件 URI 仍报错，非法扩展整组原样保留在 metadata。未编码的非 ASCII 字符须先编码；不以序列化后的 URL 与原文相等为条件，避免误拒绝合法大小写、默认端口或空 HTTP 路径。

新增 3 项回归：原评论非法字符场景与 CLI 场景先失败后通过；合法编码/IPv6/保留字符兼容性通过。覆盖尖括号、控制字符（含末尾换行）、非法组件方括号、多重 `#`/`@`、百分号编码、合法 URN、HTTP/file、userinfo、query/fragment。实际 CLI 对象打印中的非法扩展保真回退、非法文件地址非零退出且 stdout 为空、编码后的 URI 正常保留。

完整 `node --test --test-reporter=tap` 为 **98/98 通过**（Node v26.4.0）。测试仅保留 PATH/TMPDIR 环境变量，使用临时目录和回环端口。语法、源码行数、文档链接与 diff 空白检查通过。本轮未执行实际浏览器交互或远端 URI 访问，不代表完整 scheme 语义认证；没有操作原网页输入、改动仓库最外层 README 或新增工作流。

## Message content 适配与优先级

2026-11-12：完成 16.1，处理评论 [4541122141](https://github.com/inclusionAI/Avernet/pull/3906#discussion_r4541122141)，实现基于 head `0323737f7378a951007ba174217d458c0360f331`。用户明确选择：有效 role + content 的根/嵌套消息在未提供 parts 时将 content 原值放入 `params.message.parts[0].data`；同时提供时 parts 优先，content 进入 `params.message.metadata.sourceFields.content`。这是工具的业务适配规则，沿用现有 sourceFields 容器。

根/嵌套共用相同识别条件；仅业务 role、parts 或未知 role + content 等不足以识别消息的输入，继续整体 data 封装。已提供的非法 parts、明确消息的非法角色以及 metadata 保留键冲突仍报错，不通过 fallback 隐藏错误。转换表格和字段联动根据实际输出位置定位 content，Part.content 的 text 映射保留。

新增 6 项回归：5 项解析/转换/字段联动场景中 4 项在旧实现失败，1 项验证既有 parts 优先规则；新增 CLI 回归也先失败后通过。覆盖字符串、空串、0、false、null、数组、对象，根/嵌套对称性、外层业务 parts、标识沿用、metadata 保真/冲突及非法 parts 不回退。CLI 实际覆盖原评论输入、双字段输入及非法 parts，全部通过。

完整 `node --test --test-reporter=tap` **104/104 通过**（Node v26.4.0），在只保留 PATH/TMPDIR 环境变量的临时目录中执行，HTTP 测试使用临时回环端口。语法、文件行数、文档链接与 diff 空白检查通过。未重新执行浏览器双击交互，字段名/值与目标路径通过回归验证；未操作原网页输入、改动仓库最外层 README、新增工作流或调用大模型。
