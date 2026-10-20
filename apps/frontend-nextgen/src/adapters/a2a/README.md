# A2A 客户端模块（公共化边界·原地版）

Avernet 的 A2A（Agent-to-Agent）协议客户端：JSON-RPC/SSE 消费、card 能力校验、
传输选择、恢复/取消生命周期、审批（HITL）、context 禁发。本目录同时服务于
Avernet 自有前端（nextgen）并预留供其他前端仓（如内部 OCB）经 submodule 直接
消费——**但当前形态是"代码层边界"，不是独立包**；提取成独立目录/包的动作
明确推迟（决策记录见下）。

## 公共核心 vs 宿主装配

| 类别 | 文件 | 约束 |
| --- | --- | --- |
| **公共核心**（提取时整体带走） | rpc / rpcOnce / stream / card / translate / cancelOutcomes / sendBan / choiceCache / urls / recovery / A2AProviderInput / A2AProvider / sdkParser(类型段) / index / testing / v3FrameShapes | 零 SDK 运行时依赖、零浏览器窗口面、零构建期常量。由 `test/adapters/a2a/hostIsolation.test.ts` 逐文件 grep 钉死（该钉板即未来提取 PR 的**打包装清单**）。对外符号一律经 `index.ts` 单入口。 |
| **宿主装配面**（各前端自备等价物） | switch（总开关读取）/ bootstrap（app 级 choiceCache/sendBan 单例 + 五段装配）/ tcChatParser（开源 SDK 加载链 + parserFactory） | 触碰 window/localStorage/构建期 define/具体 SDK 包名是**允许且有据的**。 |

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

届时提取是机械动作，一日量级：`git mv` 公共核心 16 文件（清单=hostIsolation 钉板）→ 消费端三行 alias 接线（tsconfig / umi config / jest mapper）→ bootstrap 的四个纯函数（resolveA2aUrls / deriveEndpointBase / isLockCandidateError / isSendBannedForSession）随行迁出、bootstrap 瘦身为 glue（既有 import 面不变）→ 宿主三件各留各家。

**为何现在不提取**（2026-10 决策，spec 补节有全文）：
- frontend-nextgen 是内部导出产物（OPEN_CORE_MANIFEST），目录出仓会让 nextgen 依赖导出集外资产，内部基线下次 export 后无法构建——而内部回灌通道由另一组织运维，本仓不可验证；
- 第二消费者（内部 OCB）的 a2a 副本尚在未合入分支，"走 submodule 消费"未被真实承诺（YAGNI）。

待推进物：OCB 侧以其内部聊天 SDK 跑 `createParserContractSuite` 的结果回传。