# @avernet/a2a-chat-client

Avernet 的 A2A（Agent-to-Agent）**聊天面**客户端公共包（PR#2934 结构提取产物）：
JSON-RPC/SSE 消费、card 能力校验、传输选择策略、恢复/取消生命周期、审批（HITL）、
context 禁发、双 SDK 行为契约。17 个源文件零 npm 运行时依赖（platform API 之外），
宿主（Avernet nextgen、内部 OCB 等第二前端）经 alias 或 submodule 源码直引消费，
SDK 一律经 parserFactory 注入缝外置。nextgen 侧经 umi/tsconfig/jest 三处 alias
消费（不经 node_modules）；宿主装配面（开关/装配/SDK 适配器）留在各前端
（nextgen 形态见 apps/frontend-nextgen/src/adapters/a2a/，宿主目录钉板 hostDir.test）。

**独立验证**：`npm install && npx tsc --noEmit && npx jest --runInBand`（oracle =
devDep @tc-chat 2.1.0 + test/helpers/oracleLoader，宿主零依赖——这就是"包自含"的
运行时证明）。

## 公共核心 vs 宿主装配

| 类别 | 文件 | 约束 |
| --- | --- | --- |
| **本包**（packages/a2a-chat-client/src，17 文件） | rpc / rpcOnce / stream / card / translate / cancelOutcomes / sendBan / choiceCache / urls / recovery / A2AProviderInput / A2AProvider / sdkParser(类型段) / policy(传输选择策略纯函数) / index / testing / v3FrameShapes | 零 SDK 运行时依赖、零浏览器窗口面、零构建期常量。由 `test/hostIsolation.test.ts` 逐文件 grep **+ import 图防逃逸**双钉（相对 import 禁 '../'，闭包只见清单成员）。对外符号一律经 `index.ts` 单入口；契约经 `./testing` 子出口。 |
| **宿主装配面**（各前端自备等价物，nextgen 参考位） | switch（总开关读取）/ bootstrap（app 级 choiceCache/sendBan 单例 + 五段装配 + 包符号 re-export）/ tcChatParser（开源 SDK 加载链 + parserFactory） | 触碰 window/localStorage/构建期 define/具体 SDK 包名是**允许且有据的**；nextgen 由 hostDir.test 钉住目录=三件套。 |

## 消费契约（第二前端接入义务，四件）

注入缝已把 SDK 与宿主策略全部外化——消费方在本模块之外需要自己提供的：

1. **parserFactory**：`SdkParserFactory = (config: {enableThinking?, generateId?, xIAMToken?}) => Promise<SdkParser> | SdkParser`——构造好的解析器实例。结构契约见 `sdkParser.ts`（12 成员消费面）。**行为验收门槛**：必须通过 `testing.ts` 的 `createParserContractSuite(parserFactory)` 全部 8 组断言（nextgen 参考接线：`test/adapters/a2a/contract.tc-chat.test.ts`）。不凭 TS 类型兼容即认定行为一致。
2. **switchOn**：自持的总开关判定（读哪里由消费端定：localStorage/远程配置/构建期常量均可）。公共核心只经 `A2aBootstrapDeps.switchOn` 注入消费。
3. **app 级单例**：`TransportChoiceCache` 与 `SendBanRegistry` 由消费端创建持有并注入（`A2aBootstrapDeps.choiceCache` / provider config `sendBan`）——禁发是 context 级跨实例状态。
4. **凭证与展示配线**：`iamTokenGetter`（每请求前取新 IAM）与取消/禁发文案的 toast 消费在宿主 hooks 层（nextgen 参考位：`useBotChat.ts`）。

## 已知坑（消费端接入时）

- **老构建管线转译范围**：umi/webpack 老版本（<4.7）对 app 目录外 `.ts` 的转译规则可能比 nextgen 窄——若以 alias 指源消费，产线构建前先烟囱。
- **jest**：`@tc-chat/*` 族 ESM-only；对经 node_modules 解析的包需 `transformIgnorePatterns` 例外（或 moduleNameMapper 直接指源文件，nextgen 用的后者）。
- **无 define 环境**：公共核心不含构建期常量引用（hostIsolation 钉死）；若复制宿主三件（switch 等）需自带同义 define 或改判定源。
- **parser 加载兜底**：宿主适配器保留 `A2A_OPENCLAW_PARSER_PATH` 类环境变量位，作为免打包器的显式覆盖入口。

## 提取路径（当第二个消费者真实承诺 submodule 消费时）

提取已执行（本包即产物）：`git mv` 17 公共文件 + 13 测试套 + fixtures 与重生成脚本；消费端三行 alias（tsconfig paths / umi alias / jest mapper）；bootstrap 瘦身为 re-export + 装配（既有 import 面不变）；宿主三件各留各家。原提取路径段更新为实况。

**提取决策演进**（决策全文见 spec §9）：
- 2026-10 原地版先立"代码层边界"（hostIsolation 钉板/policy 纯化/parserFactory 注入缝），提取后置——当时的两条否决依据：frontend-nextgen 是内部导出产物，目录出仓令 nextgen 依赖导出集外资产（内部回灌通道由另一组织运维，本仓不可验证）；第二消费者未真实承诺（YAGNI）。
- 2026-10-21 owner 决策推进提取（本包即产物）：TeamClaw 导出对称性风险由 owner 接受并记录跟进（见 PR 描述"内部导出管线跟进"段）；注入缝已合入，YAGNI 保留项由 owner 判断解除。

待推进物：OCB 侧以其内部聊天 SDK 跑 `createParserContractSuite` 的结果回传（ocb 消费达成的验收件）。