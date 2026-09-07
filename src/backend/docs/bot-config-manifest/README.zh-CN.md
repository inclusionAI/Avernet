# Bot Config Manifest（Bot 配置清单）设计文档

> **状态：设计 DRAFT（讨论稿），已进入实现规划。** 本目录是「Bot 配置清单」
> 特性的设计文档；四份中文设计文档定稿后按仓库惯例补充英文版。
> 实现拆分见 `work-items.md`（英文）——其中记录了实现前需要拍板的
> 阻塞性设计问题（D1–D3）与需要外部团队回答的确认项（X1–X4）。

## 一句话说明

让用户把「这个 bot 应该具备什么配置」——MCP、工作区资源（单文件或
整个目录）、local skills、engine config、identity 文件、给模型调用的 CLI
工具，以及（在支持的引擎上）一段自定义 shell 脚本——声明在一份文档里；平台在每个生命周期边界（创建 / republish / 重建 /
显式 apply）让实际状态向它收敛。用户不感知任何引擎的目录结构与引擎间
差异。

## 核心结论（TL;DR）

配置意图分为两部分，能力边界不同：

1. **声明式 manifest**（引擎无关，所有引擎支持）：声明要什么、从哪来
   （git 引用 / `source` URL / 内联内容 / 平台注册项引用），**不写路径、
   不写命令**。业务内容多在公司 git 上以 tag 发版，故 git 源与命名源
   （一处声明、多处引用、一次 `ref` 变更原子升级整套配置）是主推形态。
2. **命令式 script**（能力受限，teclaw 明确不支持）：即 #935 已上线的
   per-bot startup script，覆盖无法声明化的长尾（沙箱内动态取数、条件逻辑）。

架构上是三层，其中交付层**零新增**：

```text
┌─────────────────────────────────────────────────────────────┐
│ ① 源文档（bot 级存储，引擎中立）                              │
│    manifest（声明式） + script（命令式，能力门控）             │
├─────────────────────────────────────────────────────────────┤
│ ② 平台侧 apply（编译/执行层，新增）                           │
│    在生命周期边界评估 manifest → guarded fetch → 落成平台实体  │
│    （与 TC Open API 同一套内部服务；GitOps 语义，声明获胜）     │
├─────────────────────────────────────────────────────────────┤
│ ③ 交付（现有机制，零新增）                                    │
│    teclaw：BotConfigArtifact 整包（现有词汇表 + cli_tools）    │
│    ARCA 系：现有 push / NAS 通道；script 走 #935 启动链        │
└─────────────────────────────────────────────────────────────┘
```

关键设计决定（详细论证见 `design.zh-CN.md`）：

- **原子能力定义在意图层**（「装一个 skill」），不是机制层（「往某路径传文件」），
  否则目录感知从后门漏回给用户。
- **manifest apply 落成真实平台实体**（路线 B），而不是 compose 时虚拟合并：
  平台视图 = 容器实况，skills-pool reconcile 天然认识这些实体，UI 可见。
- **teclaw 的 artifact 契约几乎零改动**（唯一例外是 `cli_tools`，见下）：manifest 编译产物落在
  `BotConfigArtifact` 现有词汇表内（`SkillRef` / `FileRef` /
  `McpServerRef`；engine config 走既有的 `config/teclaw.json` 文件通道，
  不新增、不启用任何 artifact 字段），URL 源由平台在组装时物化进 OSS
  store。
- **GitOps 语义**：manifest 管辖的实体每个 apply 点重新收敛，声明状态获胜，
  手工漂移被纠正；未声明的实体完全不碰。
- **私有源鉴权走租户级凭证引用**：secret 不入 manifest / script / URL，
  凭证绑定 origin、读回掩码；fetch 全在平台侧，凭证零引擎面。
- **script 部分维持 #935 的全部安全机制**（base64 / `su admin` / `__OCB_RC` /
  `mktemp`）与支持判定口径（teclaw、desktop 拒绝）。

## 文档地图

| 文档 | 内容 | 读者 |
| --- | --- | --- |
| `user-manual.zh-CN.md` | **用户手册**：怎么写清单、怎么发上去、怎么确认生效、出问题怎么查；含上手路径、逐类目写法、排错表，以及**全部端点的 API 参考**（附录 B：清单本体 / apply / 用清单创建 bot / 源凭证 / 同一份状态的另一扇门） | 业务方 & 平台团队 |
| `design.zh-CN.md` | 完整设计：动机、备选方案取舍、apply 语义、失败/安全/观测、版本化 | 平台 & 引擎团队 |
| `manifest-schema.zh-CN.md` | Manifest v1 草案：六类配置的字段、校验、到各引擎的映射 | 平台 & 引擎团队 |
| `examples.zh-CN.md` | 六类配置的完整案例：业务场景、manifest 写法、apply 动作、交付形态 | 平台 & 引擎团队 & 业务方 |
| `engine-requirements.zh-CN.md` | 各引擎的工作量与需确认清单、能力矩阵、开放问题 | 平台 & 引擎团队 |
| `work-items.md` | **实现工作项拆分**（英文）：W1–W13 每项的范围、依赖、验收标准；已定决策、设计问题与外部确认项；人员分工 | 平台团队 |
| `work-items.zh-CN.md` | 上一份的中文版，内容对齐 | 平台团队 |
| `engine-convergence-contract.zh-CN.md` | **跨引擎收敛语义契约**：应用一份 manifest 对已有状态做什么，写成对 applier 的要求（R1–R9）+ 逐类目区域表 + 自查清单 | teclaw 团队 & 引擎团队 |
| `teclaw-cli-contract.zh-CN.md` | **给 teclaw owner 的实现说明**：下发契约不变，仅新增 `cli_tools` 段。含字段定义、用例与验收清单 | teclaw 团队 |

## 术语表

| 术语 | 含义 |
| --- | --- |
| TC Open API | `/openapi/v1/...` 公开 API 面（`adapters/http/openapi_v1/`） |
| manifest | 配置清单的声明式部分（六个类别），本设计的核心新增物 |
| script | #935 的 per-bot startup script，本设计中作为配置清单的命令式部分 |
| apply 点 | 平台评估并应用 manifest 的生命周期边界（创建 / republish / 重建式 restart / 显式 apply）。**第一期实际实现的是：创建、`PUT` 之后自动跟的一次、显式 `POST …/apply`**；republish 与重建式 restart 推迟，见 `user-manual.zh-CN.md` §7 |
| 物化（materialize） | 平台把 `source` URL 的内容 fetch 下来、写入平台存储（对 teclaw 即 OSS store）的动作 |
| ARCA 系 | 走 `_build_create_bot_payload` 组装启动命令的单容器引擎家族：openclaw / claude_code / aicoding / hermes / moltis |
| teclaw | 外部容器引擎：无启动命令通道，唯一配置通道是整包 `BotConfigArtifact` |
| 收敛（converge） | 「同一份文档应用 N 次 = 应用一次」，声明式 apply 对幂等的替代表述 |

## 相关既有契约（本设计的先例与依赖）

- `docs/arch/service-skills-layout-wire-contract.md` — 「引擎无关声明 + 引擎拥有物理映射」先例
- `src/backend/src/agentclaw/community/kernel/bot_config/artifact.py` — teclaw 的 `BotConfigArtifact` 契约（本设计的编译目标之一；**除 `cli_tools` 外不改**）
- `src/backend/src/agentclaw/community/core/bot_startup_script/README.md` — #935 startup script 的设计与安全论证（本设计全盘继承）
- `src/backend/src/agentclaw/community/core/skills_pool/ports.py` — 意图层原子操作 Protocol 的先例
- `src/engine/src/engine/community/core/skills/layout_planner.py` — 引擎能力声明 + fail-closed 的先例
