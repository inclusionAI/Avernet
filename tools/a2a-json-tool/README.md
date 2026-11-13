# JSON → A2A 1.0 本地工具

支持将 Agent 配置转换为 AgentCard，以及将任意 JSON 封装成 A2A 1.0 `SendMessage` 请求。提供网页、命令行和 JavaScript 函数。无需安装第三方依赖。

## 启动网页

需要 Node.js 22 或更高版本。从 Avernet 仓库根目录运行：

```bash
cd tools/a2a-json-tool
npm start
```

打开 <http://127.0.0.1:4173>。停止服务使用 `Ctrl+C`。如端口被占用，可使用：

```bash
PORT=4174 npm start
```

网页支持标准示例、片段示例、JSON/文本文件导入、格式化、转换、错误定位、复制和下载。数据仅在浏览器内存中处理，不发送给服务端或外部 Agent；页面刷新后输入不保留。本地 HTTP 服务只监听 `127.0.0.1`。

两个 tab 顶部都有“待转换 Demo”：消息请求提供结构化消息 JSON、JSON 字段片段、Message 对象打印和业务 JSON；AgentCard 提供简化配置、JSON 字段片段和完整卡片。点击示例会替换当前输入并清除旧结果，消息示例还会重置四个消息标识选项；检查输入后点击“转换并检查”，即可对照输出与字段映射表。

在“输入 JSON”中双击字段名或字段值，“转换结果”会高亮对应字段并滚动定位，下方显示完整映射路径。支持同名字段、数组、JSON 片段及对象打印文本；字段值根据原始位置关联到所属字段，不按相同文本查找。输入修改后会清除旧高亮，再次双击时会自动转换；窄屏下会将输出滚动进页面视口。

输入和结果编辑区支持层级折叠：点击行号旁的箭头折叠对象或数组，也可点击上方的 `1`、`2`、`3` 折叠指定层级（最外层为第 1 层），点击“全部展开”恢复。光标放到 `{}`、`[]` 等括号旁会高亮配对，字符串内的括号不会误亮。双击字段联动会自动展开目标；折叠只影响展示，转换、复制和下载始终包含完整 JSON。

## 非标准 JSON 与片段

可以直接粘贴无外层括号的字段片段，以及类似 Java `toString()` 的对象打印文本，例如：

```text
agentId=travel_agent,
message=Message(role=USER,
  parts=[TextPart(kind=text, content=想去杭州玩两天, itemId=item_demo)],
  messageId=msg_demo, taskId=task_demo),
metadata={"scene":"chat"}
```

消息模式会自动识别嵌套 `message` 或根消息对象，映射 `role`、`parts` 和消息标识。普通业务对象继续放入 A2A `data`。

| 输入内容 | 输出位置 |
| --- | --- |
| `TextPart.content` | `params.message.parts[].text` |
| 未提供 `parts` 时的 Message `content` | `params.message.parts[0].data`（保留任意 JSON 值） |
| 同时提供 `parts` 和 Message `content` | 优先转换 `parts`；`content` 保留到 `params.message.metadata.sourceFields.content` |
| `USER` / `AGENT` | `ROLE_USER` / `ROLE_AGENT` |
| `messageId`、`taskId`、明确的 `contextId` | 对应 A2A Message 字段 |
| 根 Message 的 `sessionId`，或包装消息的外层 `sessionId` / `message.sessionId` | `params.message.contextId` |
| Part 的原有 `metadata` | 对应 Part 的 `metadata` |
| Part 的 `itemId`、`extParams` 等额外字段 | 对应 Part 的 `metadata.sourceFields` |
| Message 的其余非协议字段 | `params.message.metadata.sourceFields` |
| 外层除 `message` 和已映射 `sessionId` 之外的业务字段（包括外层 metadata） | `params.metadata.sourceEnvelope` |

根对象和嵌套 `message` 使用相同的消息识别规则，根消息优先。支持有效 `role + content`：未提供 `parts` 时，`content` 原值放入 `parts[0].data`，并使用 `application/json`；字符串、对象、数组、数字、布尔值和 null 均保留。两者同时提供时优先转换 `parts`，`content` 保留到 `metadata.sourceFields.content`。已提供但非法的 `parts` 仍报错，不回退到 content；已有 metadata 的保留键冲突也会报错。只有普通业务 role 或 parts 等不足以识别消息的对象继续整体放入 data。外层同名业务 `parts` 保留在 `params.metadata.sourceEnvelope.parts`，字段联动使用相同规则。

本工具采用以下业务映射规则：结构化消息中的 `sessionId` 转成 `contextId`，不再重复存入业务 metadata。内外层同时提供时必须相同，并与已提供的 `contextId`、网页选项中的 Context ID 一致；冲突或非字符串、空值会报错。Part 或已有 metadata 内的同名业务字段保持原位置；普通业务 JSON 的 data 封装规则不变。这是工具的业务适配约定，不是 A2A 定义的同名字段转换。

实际发送时，目标 Agent 需接受该 `contextId`；若同时提供 `taskId`，它必须属于同一上下文。本工具不连接 Agent，不能验证远端任务归属。`queryId` 不推断为 JSON-RPC 请求 ID。合法 `referenceTaskIds` 沿用；非 URI 或不完整 URI（如 `https://`）的业务 `extensions` 整组保留到 `metadata.sourceFields`，不声明为协议扩展。Part 的 `url` 不合法时会报错。URI 原文按组件检查允许字符及 `%HH` 编码；尖括号、控制字符或未编码的非 ASCII 字符需先编码，例如 `urn:example:%3Cext%3E`。URI 语法检查不代表资源可访问或远端支持该扩展。已有 metadata 与保留字段命名冲突时会报错，避免覆盖。

片段解析支持 `key:value`、`key=value`、嵌套 `Name(...)`、数组、嵌入 JSON、尾逗号、完整 Markdown 代码围栏；当值完整且仅缺末尾闭括号时可补齐。对象打印中可使用未加引号的 `urn:example:ext` 等 URI 值；含逗号或结构括号的值仍需加引号。网页会显示解析和字段映射说明，并提供“解析后的 JSON”预览，转换时保留原始输入。点击“格式化”会将输入替换为解析后的标准 JSON。

截断字符串、缺少值、错配括号或无法确定边界的片段仍会报错；不会执行输入代码或编造缺失内容。AgentCard 的必需字段要求仍然适用。

## AgentCard 输入

简化输入示例：

```json
{
  "name": "JSON 测试 Agent",
  "description": "接收并处理结构化 JSON 数据",
  "version": "1.0.0",
  "url": "http://localhost:8080/a2a",
  "skills": [
    {
      "id": "inspect-json",
      "name": "检查 JSON",
      "description": "处理调用方提供的 JSON 数据",
      "tags": ["json"]
    }
  ]
}
```

`url` 会转换为 `supportedInterfaces[0].url`，接口协议版本设为 `1.0`。可用 `protocolBinding` 指定绑定，默认 `JSONRPC`；支持核心绑定 `JSONRPC`、`HTTP+JSON` 和 `GRPC`。

缺省补全 `capabilities: {}`、`defaultInputModes: ["application/json"]` 和 `defaultOutputModes: ["application/json"]`，并显示补全说明。请根据实际 Agent 能力修改这些配置。名称、描述、Agent 版本、服务地址和技能不自动编造；每项技能必须提供至少一个 `tags` 标签。

也可直接输入含 `supportedInterfaces` 的 A2A 1.0 AgentCard。每个接口需提供 `url`、`protocolBinding`、`protocolVersion: "1.0"`。`version` 是 Agent 自身版本，和接口的协议版本含义不同。

示例中的 `localhost:8080/a2a` 只是占位地址，本工具没有启动该 Agent 服务。生产 HTTP 接口使用绝对 HTTPS 地址；本地测试允许 localhost、127.0.0.1 和 IPv6 回环的 HTTP 地址。

接口 URL 与 `provider.url` 都会先检查原始 URI 的字符及 `%HH` 编码；例如 `https://agent.example/a b` 必须写成 `https://agent.example/a%20b`，不会自动修改后继续输出非法原文。合法大小写、默认端口、路径规范化等差异不因此被拒绝。`provider.url` 仅校验绝对 URI 语法，不套用接口的生产 HTTPS、本机 HTTP 或禁止片段限制；公网 HTTP 网站、文档锚点和合法其他协议文档地址可保留，不验证资源是否可访问。

## 消息输入

输入可以是对象、数组、字符串、数字、布尔值或 `null`。例如：

```json
{"question":"分析这份数据","values":[1,2,3]}
```

一般业务 JSON 的转换结果使用 JSON-RPC 2.0 的 `SendMessage` 方法，数据放在 `params.message.parts[0].data`，并声明 `mediaType: "application/json"` 和 `role: "ROLE_USER"`。请求 ID 和消息 ID 默认分别生成；网页中可指定标识，也可按需填写已有 `contextId`、`taskId`。明确的消息结构按上表提取和映射。

这里只生成请求 JSON，不调用远程 Agent、不处理响应或流式事件。实际发送时需选择 AgentCard 声明的 JSON-RPC 接口，并按服务要求附上认证信息与 `A2A-Version: 1.0` 请求头。

## a2a CLI

CLI 可独立运行，无需启动网页。需要 Node.js 22 或更高版本，共用网页的解析和转换规则，不调用大模型。

从 Avernet 仓库根目录进入工具目录并注册命令：

```bash
cd tools/a2a-json-tool
npm link
a2a --help
```

随后可在任意目录执行，输入文件路径相对于当前工作目录：

```bash
# 将输入文件转换为消息请求
a2a message input.json > request.json

# 将 Agent 配置转换为 AgentCard
a2a card agent.json > agent-card.json

# 通过管道输入完整 JSON、JSON 字段片段或对象打印文本
echo '{"question":"你好"}' | a2a message
echo '"question":"你好"' | a2a message
echo 'message=Message(role=USER,parts=[TextPart(content=你好)])' | a2a message -
```

不注册命令时，也可在工具目录直接运行：

```bash
node cli.mjs --help
node cli.mjs card agent.json > agent-card.json
node cli.mjs message business.json > request.json
echo '{"question":"你好"}' | node cli.mjs message
```

省略文件参数或传入 `-` 时读取标准输入，文件也可以是 `.txt`/`.log` 中的 JSON 片段或对象打印文本。转换时标准输出只有结果 JSON，补全说明和错误写入标准错误；失败时非零退出。`--help`/`-h` 将帮助写入标准输出并成功退出，也支持 `a2a message --help` 和 `a2a card --help`。

`npm link` 将命令链接到当前检出目录，更新代码后可直接使用新版本。本次命令改名为 `a2a` 后，已安装旧命令的用户需要在工具目录重新执行 `npm link`；旧安装残留的 `a2a-json` 链接不会自动删除。若不再需要该命令，运行 `npm unlink --global json-to-a2a-local`；删除或移动检出目录后需要重新注册。

## JavaScript 复用

若要与网页/CLI 一致地支持片段和消息识别，使用：

```javascript
import { convertInput } from './web/convert-input.js';

const result = convertInput('message=Message(role=USER,parts=[TextPart(kind=text,content=你好)])', 'message');
console.log(result.output, result.notes, result.normalizedInput);
```

原有严格 JSON API 保持可用：

```javascript
import { parseJson, convertAgentCard, convertMessage } from './web/converter.js';

const data = parseJson('{"question":"你好"}');
const { output, notes } = convertMessage(data, { messageId: 'message-001' });
console.log(output, notes);
```

`convertAgentCard(input)` 和 `convertMessage(input, options)` 都返回 `{ output, notes }`，且不修改输入。`options` 可包含 `requestId`、`messageId`、`contextId`、`taskId`。转换错误的 `issues` 数组提供具体字段路径。

## 验证范围

运行自动测试：

```bash
npm test
```

本工具按固定的 [A2A v1.0.0 规范](https://a2a-protocol.org/v1.0.0/specification/) 和 [官方 a2a.proto](https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto) 实现核心字段检查，不是完整协议认证器。部分可选复杂字段会保留，并明确提示未进行深度验证。

- 不自动迁移旧版 `preferredTransport`、`additionalInterfaces`、`security` 等字段；应按 1.0 结构调整。
- 不接受带签名的 AgentCard，避免转换后继续携带失效签名。
- 不验证远程接口是否可达、能力是否真实、认证配置是否可用。
- JSON 数值按 JavaScript 数值模型处理，拒绝非有限数和超出安全整数范围的数值。大整数标识或需要精确十进制的小数应使用 JSON 字符串。

服务测试覆盖静态文件白名单、HTTP 方法和响应；命令行与转换测试覆盖有效输入、错误退出和输出结构。7 个网页 Demo 同样纳入自动测试。测试会临时监听回环端口，运行环境需允许本地监听。

## 编辑器资源维护

网页随工具包含已打包的 CodeMirror 6，不访问 CDN；普通使用仍直接 `npm start`，无需安装依赖。转换核心和 CLI 不依赖 CodeMirror。

仅在修改编辑器源码或升级固定版本依赖时运行：

```bash
npm ci
npm run build:editor
npm test
```

提交 `editor/json-editor.js`、构建脚本及必要的 `package.json`/锁文件更新，同时提交重新生成的 `web/json-editor.js` 和 `web/EDITOR-LICENSES.txt`。服务通过每次 HTML 请求独立的样式 nonce 加载编辑器样式，保持同源脚本和禁止外部连接。

## 维护入口

本工具独立于 Avernet 的产品前端、BCS 服务及 A2A SDK，不需要启动产品环境。

| 路径 | 职责 |
| --- | --- |
| `web/converter.js`、`web/message-mapper.js` | AgentCard、普通数据与结构化消息转换 |
| `web/input-parser.js`、`web/convert-input.js` | 片段解析与网页/CLI 共享入口 |
| `web/field-links.js` | 原始字段到输出字段的路径关联 |
| `web/uri.js` | 接口、提供方及消息 URI 的原文语法校验 |
| `web/app.js`、`web/index.html`、`web/style.css` | 网页交互与展示 |
| `editor/json-editor.js`、`scripts/build-editor.mjs` | 编辑器适配源码和本地资源构建 |
| `web/json-editor.js`、`web/EDITOR-LICENSES.txt` | 已打包编辑器及依赖许可证 |
| `web/examples.js` | 两种模式的合成示例 |
| `server.mjs`、`cli.mjs` | 静态服务与命令行入口 |
| `test/` | 转换、解析、示例、CLI 和 HTTP 回归 |

[交付规格](specs/2026-11-11-json-to-a2a/spec.md)、[验证记录](specs/2026-11-11-json-to-a2a/validation.md)记录此次独立工具引入的范围与证据。修改工具后，在本目录运行 `npm test` 完成本地验证。
