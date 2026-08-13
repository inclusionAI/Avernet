# Bot Startup Provisioning（启动置备）设计文档

> **状态：DRAFT（讨论稿）。** 本目录是「启动置备」特性的设计文档。
> 尚未进入实现阶段；定稿后按仓库惯例补充英文版。

## 一句话说明

让用户把「这个 bot 启动完成时应该具备什么」——MCP、工作区资源文件、local
skills、engine config、identity 文件，以及（在支持的引擎上）一段自定义 shell
脚本——存放在 bot 上；平台在每次容器/实例重建时自动将其变成现实。用户不感知
任何引擎的目录结构与引擎间差异。

## 核心结论（TL;DR）

置备意图分为两部分，能力边界不同：

1. **声明式 manifest**（引擎无关，所有引擎支持）：声明要什么、从哪来
   （`source` URL / 内联内容 / 平台注册项引用），**不写路径、不写命令**。
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
│    teclaw：BotConfigArtifact 整包（现有词汇表，schema 不动）   │
│    ARCA 系：现有 push / NAS 通道；script 走 #935 启动链        │
└─────────────────────────────────────────────────────────────┘
```

关键设计决定（详细论证见 `design.zh-CN.md`）：

- **原子能力定义在意图层**（「装一个 skill」），不是机制层（「往某路径传文件」），
  否则目录感知从后门漏回给用户。
- **manifest apply 落成真实平台实体**（路线 B），而不是 compose 时虚拟合并：
  平台视图 = 容器实况，skills-pool reconcile 天然认识这些实体，UI 可见。
- **teclaw 的 artifact 契约零改动**：manifest 编译产物落在
  `BotConfigArtifact` 现有词汇表内（`SkillRef` / `FileRef` / `McpServerRef` /
  `engine_overrides`），URL 源由平台在组装时物化进 OSS store。
- **GitOps 语义**：manifest 管辖的实体每个 apply 点重新收敛，声明状态获胜，
  手工漂移被纠正；未声明的实体完全不碰。
- **script 部分维持 #935 的全部安全机制**（base64 / `su admin` / `__OCB_RC` /
  `mktemp`）与支持判定口径（teclaw、desktop 拒绝）。

## 文档地图

| 文档 | 内容 | 读者 |
| --- | --- | --- |
| `design.zh-CN.md` | 完整设计：动机、备选方案取舍、apply 语义、失败/安全/观测、版本化 | 平台 & 引擎团队 |
| `manifest-schema.zh-CN.md` | Manifest v1 草案：六类配置的字段、校验、到各引擎的映射 | 平台 & 引擎团队 |
| `engine-requirements.zh-CN.md` | 各引擎的工作量与需确认清单、能力矩阵、开放问题 | 平台 & 引擎团队 |

## 术语表

| 术语 | 含义 |
| --- | --- |
| TC Open API | `/openapi/v1/...` 公开 API 面（`adapters/http/openapi_v1/`） |
| manifest | bot 级存储的声明式置备文档，本设计的核心新增物 |
| script | #935 的 per-bot startup script，本设计中作为置备文档的命令式部分 |
| apply 点 | 平台评估并应用 manifest 的生命周期边界（创建 / republish / 重建式 restart / 显式 apply） |
| 物化（materialize） | 平台把 `source` URL 的内容 fetch 下来、写入平台存储（对 teclaw 即 OSS store）的动作 |
| ARCA 系 | 走 `_build_create_bot_payload` 组装启动命令的单容器引擎家族：openclaw / claude_code / aicoding / hermes / moltis |
| teclaw | 外部容器引擎：无启动命令通道，唯一配置通道是整包 `BotConfigArtifact` |
| 收敛（converge） | 「同一份文档应用 N 次 = 应用一次」，声明式 apply 对幂等的替代表述 |

## 相关既有契约（本设计的先例与依赖）

- `docs/arch/service-skills-layout-wire-contract.md` — 「引擎无关声明 + 引擎拥有物理映射」先例
- `src/backend/src/agentclaw/community/kernel/bot_config/artifact.py` — teclaw 的 `BotConfigArtifact` 契约（本设计的编译目标之一，**不改**）
- `src/backend/src/agentclaw/community/core/bot_startup_script/README.md` — #935 startup script 的设计与安全论证（本设计全盘继承）
- `src/backend/src/agentclaw/community/core/skills_pool/ports.py` — 意图层原子操作 Protocol 的先例
- `src/engine/src/engine/community/core/skills/layout_planner.py` — 引擎能力声明 + fail-closed 的先例
