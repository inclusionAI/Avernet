# 引擎实际能力矩阵

本文档不是 `EngineCapabilities` 声明的简单转录，而是根据当前 Avernet 代码
（`dev` 提交 `a9b62a1f2771ec6cdb3871a12dc80ac3c48fcd86`，2026-11-02）逐项核对能力声明、
引擎装配和对应服务实现后得到的代码审计结果。

下文路径均相对仓库根目录；`<src>` 代表 `engine/adapter/src/engine/community`。

## 判定口径

| 符号 | 含义 |
| --- | --- |
| ✅ | 能力已由该引擎声明，并且存在可执行的对应实现 |
| ❌ | 未声明、未装配、只有占位实现、固定返回失败，或明确抛出“不支持” |

矩阵只使用 ✅ 和 ❌。确实可用但有适用范围或行为限制的能力仍标记为 ✅，具体限制列在
“可用但受限的能力”中。内部存在方法但没有进入引擎能力契约的能力标记为 ❌，因为消费者
会按照 `EngineCapabilities` 进行能力发现和拦截（HTTP 层通过 `<src>/api/caps.py:24-47`
的 `check_capability` 返回 501）。

`file.write` 目前没有独立的 `FileService` 方法或 API 路由；现有写入操作由
`file.upload` 承担，因此所有引擎的 `file.write` 都按 ❌ 记录。协议定义见
`<src>/core/file/protocol.py:31-91`，路由见 `<src>/api/file/router.py:42-221`。

## 引擎范围

| 矩阵列 | 引擎变体 | 加载 Profile | 主要实现 |
| --- | --- | --- | --- |
| OpenClaw | `openclaw` | community、test、corp | `<src>/engines/openclaw/engine.py:65-212` |
| Claude Code | `claude_code` community | community、test | `<src>/engines/claude_code/engine.py:55-183` |

引擎加载逻辑见 `<src>/engines/__init__.py:41-63`。

**与上一版相比，本版不再包含 AI Coding、Hermes、DeepSeek Harness SDK 和
DeepSeek Harness SDK Minimal 四列。** 这些引擎只在 corp Profile 下从
`engine.corp.engines.*` 加载，而 corp 代码只存在于 OCB 内部仓库，不在 Avernet 开源
仓库中（`<src>/engines/__init__.py:52-56` 以 `_optional_import_module` 静默跳过）。
本次无法对它们做代码审计，因此不沿用上一版结果。如需更新这四列，需要在 OCB 内部仓库
中按同一口径复核。

共享 Capability 枚举位于 `<src>/core/engine/capability.py:16-158`。

## 能力矩阵

### 会话（Session）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| session.list | ✅ | ✅ |
| session.create | ✅ | ✅ |
| session.delete | ✅ | ✅ |
| session.update | ✅ | ✅ |
| session.history | ✅ | ✅ |
| session.archive | ❌ | ❌ |

### 对话（Chat）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| chat.stream | ✅ | ✅ |
| chat.complete | ❌ | ❌ |
| chat.abort | ✅ | ✅ |
| chat.approval | ✅ | ✅ |
| chat.history | ✅ | ✅ |
| chat.interaction | ❌ | ✅ |
| chat.mode_transition | ❌ | ✅ |

### MCP

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| mcp.list | ✅ | ✅ |
| mcp.create | ✅ | ✅ |
| mcp.update | ✅ | ✅ |
| mcp.delete | ✅ | ✅ |
| mcp.start | ❌ | ❌ |
| mcp.stop | ❌ | ❌ |
| mcp.tools.list | ❌ | ✅ |
| mcp.tools.call | ✅ | ✅ |
| mcp.resources.list | ❌ | ❌ |
| mcp.resources.read | ❌ | ❌ |
| mcp.prompts.list | ❌ | ❌ |
| mcp.prompts.get | ❌ | ❌ |
| mcp.filter_servers | ✅ | ✅ |

### 技能（Skills）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| skills.list | ❌ | ✅ |
| skills.install | ❌ | ✅ |
| skills.uninstall | ❌ | ✅ |
| skills.update | ❌ | ✅ |
| skills.execute | ❌ | ❌ |
| skills.discover | ❌ | ❌ |
| skills.sync_symlinks | ✅ | ✅ |
| skills.sync_bindpaths | ✅ | ✅ |
| skills.clean_symlinks | ✅ | ✅ |
| skills.center_ensure | ✅ | ✅ |
| skills.local_package.apply.v1 🆕 | ✅ | ✅ |

### CLI 工具

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| cli.install | ✅ | ✅ |
| cli.delete | ✅ | ✅ |
| cli.list | ✅ | ✅ |
| cli.replace | ✅ | ✅ |
| cli.download | ✅ | ✅ |
| cli.acli.install 🆕 | ✅ | ✅ |
| cli.acli.uninstall 🆕 | ✅ | ✅ |
| cli.acli.cancel 🆕 | ✅ | ✅ |
| cli.acli.list 🆕 | ✅ | ✅ |
| cli.acli.operation.get 🆕 | ✅ | ✅ |

### 默认配置

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| default_config.get | ✅ | ❌ |

### 网页终端（Web Shell）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| web_shell.open | ✅ | ❌ |

### 审批（Approval）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| approval.get | ✅ | ❌ |
| approval.set | ✅ | ❌ |
| approval.list | ❌ | ❌ |

`GET /approvals/modes`（`<src>/api/approvals/router.py:104-125`）返回的是写死的模式枚举，
不经能力网关，也不调用任何引擎服务，因此不算 `approval.list`。

### 文件（File）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| file.read | ✅ | ✅ |
| file.write | ❌ | ❌ |
| file.upload | ✅ | ✅ |
| file.delete | ✅ | ✅ |
| file.list | ✅ | ✅ |
| file.mkdir | ❌ | ❌ |

### Bash

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| bash.exec | ✅ | ✅ |

### 节点（Node）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| node.list | ✅ | ❌ |
| node.register | ❌ | ❌ |
| node.unregister | ❌ | ❌ |
| node.status | ❌ | ❌ |

### 通道（Channel）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| channel.config.get | ❌ | ❌ |
| channel.config.set | ❌ | ❌ |
| channel.status | ❌ | ❌ |

### 定时任务（Cron）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| cron.list | ✅ | ✅ |
| cron.create | ✅ | ✅ |
| cron.update | ✅ | ✅ |
| cron.delete | ✅ | ✅ |
| cron.run | ✅ | ✅ |
| cron.history | ✅ | ✅ |

### 模型（Model）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| model.list | ✅ | ✅ |
| model.switch | ✅ | ✅ |
| models.default.get 🆕 | ✅ | ❌ |

### 健康检查（Health）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| health.check | ❌ | ❌ |
| health.components | ❌ | ❌ |
| health.metrics | ❌ | ❌ |

引擎生命周期接口本身有 `health_check()`，但默认实现直接返回健康
（`<src>/core/engine/base.py:322-325`），两个引擎都没有覆盖，也都没有把它作为
`Capability.HEALTH_CHECK` 对外声明；`health.components` 和 `health.metrics` 也没有对应插件实现。
Adapter 的 `GET /health`（`<src>/api/app.py:370`）是进程存活探针，不属于引擎能力。
因此这里仍记为 ❌。

### 效果评估（Effect）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| effect.track | ❌ | ❌ |
| effect.evaluate | ❌ | ❌ |
| effect.feedback | ❌ | ❌ |
| effect.report | ❌ | ❌ |

### 自动发起（Auto Initiate）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| auto_initiate.execute | ❌ | ❌ |

`BaseEngine.auto_initiate` 槽位存在（`<src>/core/engine/base.py:301-307`），但两个引擎都没有赋值。

### 数据代理（Data Proxy）

| 能力 | OpenClaw | Claude Code |
| --- | --- | --- |
| data_proxy.forward | ❌ | ❌ |

`/data/*` 路由（`<src>/api/aicoding/data_proxy_router.py:103`）存在，但只有 corp 的 AI Coding
引擎声明该能力；在开源仓库中的两个引擎下，该路由经能力网关返回 501。

🆕 表示上一版矩阵之后新增到 `Capability` 枚举的能力。

## 可用但受限的能力

以下能力仍在矩阵中标记为 ✅，因为对应代码路径确实可以执行。

| 引擎变体 | 能力 | 实际限制 |
| --- | --- | --- |
| Claude Code | session.create | Relay 没有显式 `sessions.create`；先通过 `sessions.patch` 预留 session key，首次 `chat.send` 时建立会话。声明见 `<src>/engines/claude_code/engine.py:118-121`，实现见 `<src>/core/adapters/claude_code/session.py:327-370`。 |
| Claude Code | mcp.tools.call | 只有 SDK bridge 可直接调用；CLI bridge 需要通过对话让模型调用工具。声明见 `<src>/engines/claude_code/engine.py:122-124`。 |
| Claude Code | mcp.tools.list | 必须指定 `server_code`；未指定时直接返回空列表。另外 Adapter 当前没有对外的 tools.list HTTP 路由，只能通过服务接口调用。见 `<src>/core/adapters/claude_code/mcp.py:249-260`。 |
| Claude Code | skills.list / install / uninstall / update | `SkillsService` 方法有真实 relay 调用，但 Adapter 的 `/api/skills` 路由只暴露批量 symlink、center ensure、本地包应用和布局类接口，没有逐个技能管理的 HTTP 入口。实现见 `<src>/core/adapters/claude_code/skills.py:181-231`，路由见 `<src>/api/skills/router.py:156-616`。 |
| OpenClaw、Claude Code | model.switch | 没有独立的切换 RPC；由 `chat.send` 的 `model` 参数或 `session.update` 的模型字段生效，本质是逐请求/逐会话设置。见 `<src>/core/models/protocol.py:15-18`。 |
| OpenClaw | models.default.get | 只读取引擎 `config.get` 中配置的默认模型，不解析会话/请求级覆盖；未配置时返回 `None`。见 `<src>/core/adapters/openclaw/models.py:183-187`、`<src>/core/models/default_model.py:1-8`。 |
| OpenClaw、Claude Code | skills.local_package.apply.v1 | 由引擎无关的 `LocalSkillPackageApplication` 在本地文件系统上完成，不经过引擎运行时。见 `<src>/core/adapters/openclaw/skills.py:112-117`、`<src>/core/adapters/claude_code/skills.py:172-177`。 |
| OpenClaw、Claude Code | cli.acli.* | 由引擎无关的 `AcliRunner` / `AcliOperations` 执行，安装目录为全局常量 `ACLI_INSTALL_DIR=/home/admin/.agentclaw/bin`，不区分引擎；同一容器同时只允许一个操作（否则 409 `engine_busy`）。见 `<src>/api/cli/acli.py:1-178`、`<src>/config.py:47`。 |
| OpenClaw、Claude Code | cli.* | `LocalCliToolsService` 只做本地文件放置，目录由镜像决定：OpenClaw 为 `/home/admin/.openclaw/cli`，Claude Code 为 `/home/admin/.aicoding/cli`。见 `<src>/engines/openclaw/engine.py:63`、`<src>/engines/claude_code/engine.py:135`。 |
| OpenClaw、Claude Code | bash.exec | 两个引擎都复用核心默认的 `BaseBashService`，在 Adapter 进程所在环境执行，而不是引擎原生能力。 |

## 声明与实现不一致

| 引擎变体 | 能力 | 代码审计结论 |
| --- | --- | --- |
| OpenClaw | chat.complete | 声明为支持，但 `ChatService` 已删除 `complete()`，并明确说明没有生产实现。见 `<src>/core/chat/protocol.py:17-20`。 |
| OpenClaw | mcp.start、mcp.stop | 声明为受限支持（“通过 mcporter 命令启动/停止”），但对应方法固定返回 `False`。见 `<src>/core/adapters/openclaw/mcp.py:192-205`。 |
| OpenClaw | mcp.tools.list、MCP resources/prompts | 声明为支持，但实现直接抛出 `CapabilityNotSupportedError`。见 `<src>/core/adapters/openclaw/mcp.py:220-270`。 |
| OpenClaw | node.register、node.status | 声明为支持，但 Node 适配器和路由只有 `list_nodes`。见 `<src>/core/adapters/openclaw/node.py:55-80`、`<src>/api/node/router.py:44-69`。 |
| OpenClaw | Channel 三项能力 | 声明为支持，但代码中没有 Channel 服务、插件槽或路由实现。声明位置为 `<src>/engines/openclaw/engine.py:134-137`。 |
| OpenClaw | file.write | 声明了 `FILE_WRITE`，但共享 `FileService` 只有 count/upload/read/remove/rmtree/list_dir，且 API 只对 `FILE_UPLOAD` 提供写入路由。声明见 `<src>/engines/openclaw/engine.py:124`。 |
| Claude Code | skills.execute | 声明为受限支持，但方法固定返回 `success=False`，实际只能改走 chat。见 `<src>/core/adapters/claude_code/skills.py:252-269`。 |
| Claude Code | MCP resources/prompts、skills.discover | Adapter 中存在实现（且 relay 插件有对应方法），但未进入该引擎的能力声明，因此能力发现结果仍是不支持。MCP 实现见 `<src>/core/adapters/claude_code/mcp.py:281-349`，技能发现见 `<src>/core/adapters/claude_code/skills.py:290-298`。 |
| Claude Code | file.write | 同 OpenClaw：声明了 `FILE_WRITE`，但没有对应方法或路由。声明见 `<src>/engines/claude_code/engine.py:110`。 |

## 与上一版的差异

只比较两版都覆盖的 OpenClaw 和 Claude Code 两列：

- 新增能力：`skills.local_package.apply.v1`、`cli.acli.*`（5 项）在两个引擎上均为 ✅；
  `models.default.get` 仅 OpenClaw 为 ✅。
- 已有能力的判定结果没有变化，上一版列出的声明与实现不一致项全部仍然存在。
- 移除 AI Coding、Hermes、DeepSeek Harness 两个 Profile 共四列，原因见“引擎范围”。

## 维护规则

更新本矩阵时不能只比较 supported、limited 和 fallback 集合，还必须同时检查：

1. 引擎是否实际装配了对应服务；
2. 对应方法是否执行真实操作，而不是占位、固定失败或“不支持”异常；
3. 能力是否进入该引擎的 `EngineCapabilities` 契约；
4. Profile 是否会改变服务装配或能力声明，例如 corp 与 community 下的 `claude_code` 是两个不同实现。

`BaseEngine.validate_capabilities()`（`<src>/core/engine/base.py:344-391`）只做插件槽粒度的
检查（“声明了某个领域的能力就必须装配该槽位”），不会发现上面列出的方法级不一致。

本次是静态代码审计，没有连接 OpenClaw Gateway 或 Claude Code Relay 做端到端冒烟测试，
也没能在本地运行 `validate_capabilities()`（当前环境无法安装 Adapter 依赖）；
因此 ✅ 表示代码路径完整，不代表外部服务在当前环境中一定可用。
