---
agent: tc-review
status: completed
created: 2026-08-31T21:08:02+08:00
iteration: 1
---

# 系分 Spec: Default 能力集 CLI 安装、AgentPass 授权与 Caller/Owner 配置

前端交互与接口使用说明见 [005-frontend-interaction.md](005-frontend-interaction.md)。

## 需求概述

一期为 `openclaw`、以及 `claude_code + generalCC` Bot 提供 YAML 驱动的 Default CLI 策略：每次容器启动时，Backend 从 AgentPass 查询历史 CLI scope，补齐 YAML 规定的必选 CLI（`dataphin` 与 `deepinsight-cli`），保留已有 CLI 及其 `identity_mode`，连同完整 MCP scope 全量更新 AgentPass。设备启动脚本随后根据同一 YAML 安装已注册且受 YAML catalog 管理的 CLI，并在容器本地记录安装状态。CLI 仅属于 Default 能力集；一期新增与 MCP 同构的稀疏 `caller` 覆盖表和切换 API，UI 能显示 CLI 的执行身份。

本期不创建 `ac_bot_cli_capability`，不支持非 Default 能力集新增 CLI，也不删除历史 AgentPass CLI。

## 用户确认与范围澄清

用户已确认 AgentPass 已支持 CLI `identity_mode`，一期继续开放已授权 CLI 的 `caller/owner` 配置接口。本期交付的是 AgentPass authorization configuration：CLI caller/owner 写入独立 sparse 配置并通过完整 Passport scope 更新 `identity_mode`；任一有效 MCP 或 CLI 为 `caller` 时，Bot aggregate `call_type` 为 `caller`，并进入既有 IAM caller-token 选择流程。

OpenClaw 与 `claude_code/generalCC` 的最终 CLI 执行 principal 是否消费该 AgentPass 字段，仍须在真实容器的端到端验收中确认；在此之前不得将该 E2E 结果表述为已在本仓验证。

## 已确认的既有链路与问题

1. [create_flow.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/bot_management/create_flow.py:569) 和 `create_bot_for_others_service.py` 创建时调用 `get_default_cli_items()`；[\_defaults.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/mcp/services/_defaults.py:273) 只有 `aicoding` 的 9 个硬编码 CLI。`claude_code/generalCC` 当前会被默认能力分桶映射到 `aicoding`；本期保持这一创建期行为，首次注册旧 9 项，随后启动时补齐两个 YAML 受管 CLI，且不得清除旧项。
2. [sync_service.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:933) 已有“历史 AgentPass CLI 优先、默认 CLI 补齐”的合并，但默认来源仍为硬编码；[bot_runtime_projector.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py:313) 直接把 AgentPass CLI 作为 Default CLI 及运行时命令来源。
3. [plugin_api/passport.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/plugin_api/passport.py:41) 的 `CliItem` 与 `extract_cli_items()` 当前仅保留 `cli_code/cli_name/cli_desc`，会丢弃 AgentPass 已支持的 CLI `identity_mode`。而 `updatePassport` 为 MCP/CLI scope 的覆盖式更新，现有 MCP 已专门携带完整 `mcp_items` 以避免 Caller 被静默降为 Owner。
4. [skill_set_management_service.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/skill_center/services/skill_set_management_service.py:777) 只把 AgentPass CLI 返回给 Default Set；这正是一期 UI 展示边界。
5. 设备启动已在 [start_service.sh](/Users/helloworld/Desktop/codes/teamclaw_worktrees/daas-script_worktree/cli-default-install-identity/bootstrapping/start_service.sh:550) 调用 `bootstrap_device_auth.sh`，其 Backend 端点为 [devices/router.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/adapters/http/devices/router.py:823)。调用完成后才进入依赖安装；`openclaw` 和 `claude_code` 均会走此路径。当前 Bootstrap 响应只包含 `agent_code`。
6. MCP 的 `ac_bot_mcp_call_config` 是稀疏 Caller override，但同时驱动 Bot 级 `call_type` 与 IAM 换签；其仓储明确禁止 aggregate 从 Caller 降回 Owner。本期将 `ac_bot_cli_call_config` 一并纳入该 aggregate：MCP/CLI 以资源级 map 决定自身 `identity_mode`，但任一有效 caller 都会令 Bot aggregate 为 caller。

## 编码 Spec

### 功能点

- [ ] 新增受严格 schema 校验的 CLI capability YAML，精确支持 `openclaw` 和 `claude_code/generalCC` 两个 profile；两者的 Default 必选 CLI 均为 `dataphin` 与 `deepinsight-cli`。
- [ ] 每次设备 Bootstrap 时执行 Backend CLI scope 收敛：查询历史 AgentPass CLI、保留其元数据和 `identity_mode`、补齐 profile 必选项、叠加本地 CLI caller override，并把完整 MCP + CLI scope 一次性更新 AgentPass。
- [ ] `CliItem`、Passport adapter、Default 能力集响应完整透传 CLI `identity_mode`。
- [ ] 新增 `ac_bot_cli_call_config` 稀疏 Caller override 和同组 OpenAPI 切换接口；默认 Owner 由缺失行表达，切回 Owner 删除该行。
- [ ] 启动脚本引入通用 CLI installer：依据 Bootstrap 返回的 AgentPass CLI 清单与同版本 YAML catalog，统一 bootstrap `acli`，再安装 `dataphin`、`di`；已安装且探测通过则跳过。
- [ ] 设备本地保存安装状态；安装失败阻断受配置 CLI 的引擎启动，但不回滚 AgentPass 已存在的授权。

### YAML 与配置契约

YAML 是 Default CLI、CLI 元信息和安装声明的唯一业务来源，不能继续为本期 profile 使用 `get_default_cli_items()` 的 `aicoding` 硬编码列表。部署产物必须把**字节一致、带 `manifest_version` 的同一 manifest**提供给 Backend 与 DaaS：Backend 用它决定 AgentPass 必选项，DaaS 用它决定可安装项。禁止两仓人工维护语义可能不同的副本；发布/CI 必须比较 canonical SHA-256 或由同一构建步骤生成 DaaS 副本。

建议 canonical 路径为 Avernet `src/backend/src/agentclaw/community/configs/cli-capabilities.yaml`，设备侧最终路径为 `/home/admin/agentclaw-daas-scripts/confs/cli-capabilities.yaml`；若采用 bootstrap 下发，落地文件也必须与 Backend manifest 的版本和摘要一致。manifest 不含 token、cookie 或用户输入。

```yaml
version: 1
manifest_version: "2026-08-31.1"
install_root: "/home/admin/.agentclaw/cli"
managed_bin_dir: "/home/admin/.agentclaw/bin"

profiles:
  - id: openclaw-default
    match:
      engine_type: openclaw
    default_cli_codes: [dataphin, deepinsight-cli]
  - id: claude-code-generalcc-default
    match:
      engine_type: claude_code
      template_type: generalCC
    default_cli_codes: [dataphin, deepinsight-cli]

installers:
  acli:
    bootstrap:
      url: "https://artifacts.antgroup-inc.cn/t/MAIN_SITE/artifact/repositories/ant-cli-common/cli/acli/install/install.sh"
      sha256: "<REQUIRED_RELEASE_SHA256>"
    probe_argv: [acli, --version]

catalog:
  dataphin:
    cli_name: dataphin-cli
    cli_desc: Dataphin 命令行工具
    executable: dataphin
    default_identity_mode: owner
    install:
      installer: acli
      argv: [install, dataphin]
    probe_argv: [dataphin, --version]
  deepinsight-cli:
    cli_name: deepinsight-cli
    cli_desc: DeepInsight 命令行工具
    executable: di
    default_identity_mode: owner
    install:
      installer: acli
      argv: [install, di]
    probe_argv: [di, --version]
```

匹配必须为精确逻辑引擎与模板匹配；不可因 `claude_code` 的内部 runtime 映射而把 `normalCC` 或其他 template_type 误配到 `generalCC`。未命中 profile 时 `required_cli_items=[]`，不得新增 YAML CLI。`catalog` 的 `cli_code`、可执行文件名、installer 引用和 argv 都必须在加载期校验；同一 profile 不得重复 code。

### 技术方案

#### 1. Backend scope 收敛与启动顺序

```text
设备 bootstrap-auth
  -> 校验 device_id + bot_id + owner_id 绑定
  -> 读取 Bot active_engine/template_type，解析 YAML profile
  -> queryAgentPassport 获取完整历史 CLI（包含 identity_mode）
  -> 历史 CLI + YAML required CLI（按 cli_code 去重，历史优先）
  -> 稀疏 ac_bot_cli_call_config 的 caller 行覆盖 identity_mode
  -> 解析完整有效 MCP scope 与 MCP identity_mode
  -> updatePassport(mcp_items + cli_items 全量覆盖)
  -> Bootstrap 返回 agent_code、收敛后的 cli_codes、manifest_version/digest
  -> start_service.sh 校验本地 YAML version/digest，调用通用 installer
  -> probe -> bootstrap acli（必要时） -> acli install -> probe -> 启动引擎
```

新增 `CliCapabilityManifestResolver`（纯读取/校验）和 `CliPassportScopeReconcileService`（业务编排）。后者是此需求所有 CLI scope 写入的唯一入口。其合并顺序固定为：

```python
merged_codes = dedupe_by_cli_code(historical_agent_pass_clis, required_yaml_clis)
identity = cli_sparse_override or historical.identity_mode or yaml.default_identity_mode
```

其中 `historical_agent_pass_clis` 的 code/name/desc/identity 绝不被同 code YAML 的展示文案覆盖；YAML 仅为历史缺少的 code 创建 `{cli_code, cli_name, cli_desc, identity_mode: owner}`。历史中不在 catalog 的 CLI 仍原样保留和回写，但 DaaS 仅记录 `unmanaged_catalog_entry` 并跳过安装，绝不可尝试猜测安装命令。

必须用当前 MCP 的实际 effective entries + `ac_bot_mcp_call_config` 解析出的 identity 构成完整 `mcp_items`，再和全量 `cli_items` 一次调用 `PassportPlugin.update_passport()`。任何获取 MCP identity 或 AgentPass CLI 失败均不得发送半份 scope。成功且 scope 无变化可以跳过外部更新；发生新增或 identity 覆盖时必须更新。

Backend 扩展已有 `/api/v1/devices/callback/bootstrap-auth`，保持旧的 `agent_code` 字段兼容，并额外返回非敏感 `cli_manifest_version`、`cli_manifest_digest`、`cli_codes`（必要时 `cli_items`）。`bootstrap_device_auth.sh` 解析完整 JSON 到受限临时文件，`start_service.sh` 将其传给 installer；不得把 caller/owner、token、完整 Bootstrap body写入日志。

创建、修复、MCP 同步、能力集 runtime projection、删除 CLI 等所有现有 overwrite writer 都必须迁移到共享 scope builder/reconciler 或传入其完整 CLI snapshot：

- `claude_code/generalCC` 创建路径保持既有 `get_default_cli_items()` 行为：首次 Passport scope 包含 aicoding 九项；首次/每次 Bootstrap 在保留这些历史项的基础上补齐 YAML 管理的 `dataphin`、`deepinsight-cli`，不得清除或替换创建期项。
- [sync_service.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/mcp/services/sync_service.py:925)、[bot_runtime_projector.py](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity/src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py:295)、删除 Default CLI endpoint 与 default passport repair 均不可另行调用硬编码默认列表或丢掉 CLI identity。
- 不在一期 profile 的引擎维持既有行为；本变更不得把 YAML 默认项扩展到 `aicoding`、`moltis`、`hermes` 或其他 Claude template。

`RuntimeProjection.cli_commands` 仍仅携带授权的 `cli_code`，不承担 caller token 注入或 CLI 安装状态；它继续从收敛后的 Passport snapshot 得到 CLI code。

#### 2. CLI caller/owner 管理语义

新增与 MCP 调用身份 router 同级的平铺 endpoint：

```http
PATCH /openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type
Content-Type: application/json

{"call_type":"caller"}
```

响应返回 `cli_code` 和最终 `call_type`。API 不创建 CLI、不改变 skill set，不接受 owner/caller 用户 ID。仅 active service Bot 的 owner 可改写，沿用 MCP caller 配置的 Bot 状态、owner、协作者锁检查与错误语义；个人 Bot 或无权调用必须拒绝。`cli_code` 必须已存在于该 Bot 当前 AgentPass CLI scope，未知 code 返回 not found。更新顺序为：受锁保护地保存/删除 sparse row -> 读取完整 scope 且 overlay override -> 全量更新 AgentPass；AgentPass 更新失败时在同一锁约束下补偿上一 sparse 状态并返回同步失败。

CLI caller/owner 同时参与 `ac_bots.call_type` 和 `caller_config_revision`：任一有效 MCP 或 CLI sparse caller row 存在时，aggregate 为 caller。CLI 的资源级 `identity_mode` 仍只覆盖对应 `cli_code`，不改变任一 MCP row；但 aggregate 与既有 MCP 共享 caller -> owner 的不可逆保护。因此最后一个有效 caller 是 CLI 时，CLI 切回 owner 返回 `CALLER_TO_OWNER_UNSUPPORTED` 并在事务中回滚 sparse row；若仍有任一 MCP/CLI caller，则可将该 CLI 切回 owner，aggregate 保持 caller。

AgentPass 已支持 CLI `identity_mode` 是一期授权配置的前提。用户已确认即使 OpenClaw/Claude Code 的真实 CLI 执行面尚未完成端到端验收，一期仍暴露/接受受支持 profile 中已授权 CLI 的 caller 配置；不得把“已写 AgentPass 配置”伪称为 CLI 执行 principal 已生效。CLI caller 会使 Bot aggregate 进入既有 IAM caller-token 选择流程，但该 aggregate 行为不替代目标 CLI 对 AgentPass `identity_mode` 的执行面消费验证。

#### 2.1 MCP / CLI 统一 Caller Context 读取

前端读取单项 MCP、CLI 的 caller/owner 配置统一使用既有 Caller Context，而不从能力集资源接口的 `identity_mode` 推断身份：

```http
GET /openapi/v1/bots/{bot_id}/caller-context
```

OpenAPI 与兼容的 `/api/bots/{bot_id}/caller-context` 均在原有 `mcp_call_types` 旁增加 `cli_call_types: {cli_code: "caller"}`。两个 map 同为**稀疏 override**：只返回该 Bot 当前 engine 下显式持久化的 `caller` 项；资源列表中不存在于 map 的 MCP 或 CLI 必须按 `owner` 展示。CLI map 只读取本地 `ac_bot_cli_call_config`，不在该只读接口发起 AgentPass 请求，也不返回 token、安装状态或完整 Passport scope。

能力集资源接口继续仅提供 Default Set 的 CLI 清单、名称与说明；前端以 `cli_code` 将该清单与 `cli_call_types` 合并，不能直接以资源响应中的 `identity_mode` 决定选择器状态。这样 MCP 与 CLI 的 caller 状态均有同一授权、同一 stage、同一 `editable` 语义和同一错误边界。

#### 3. DaaS 通用安装器

在 `agentclaw-daas-scripts` 新增一个独立的 Python 安装器（例如 `bootstrapping/install_managed_clis.py`）和其单测，而不是把 YAML 逻辑堆在 `start_service.sh`。`start_service.sh` 在 Bootstrap 成功、现有并行安装 Join point 完成、任何 engine/finalize 启动前，以固定 argv 调用它；此位置同时覆盖 OpenClaw 和 Claude Code。

安装器职责仅限于：校验 YAML/Bootstrap manifest version+digest、从 AgentPass 返回的 code 中选择 catalog 管理项、维护 `/home/admin/.agentclaw/cli/install-state.json`、依次 probe/bootstrap/install/verify，并将 managed bin 目录安全加入本次引擎启动 PATH。installer 的所有参数来自部署配置或 Backend 回包，不能来自模型、聊天内容或 CLI HTTP API。

- `acli --version` 成功且状态与 manifest 一致时跳过 bootstrap；否则下载 URL 到 `mktemp` 文件，校验 release SHA-256 后才 `bash <temp-file>`，随后立刻再次 probe。
- 对每个 code，状态与 manifest digest 匹配且 `probe_argv` 成功才跳过；否则执行固定 `acli install dataphin` / `acli install di`，再 probe 并原子写回状态。probe 或 install 失败必须使本次启动写 `FAILED` 并退出，不能启动一个授权了却不可执行的受管 CLI Bot。
- 禁止 `curl | bash`、`eval`、`sh -c`、YAML shell 字符串和未受控插值。现有 `run_task_async` 内部使用 `eval`，本安装器不得经该函数注册；只能使用 `python3 ... --manifest ... --codes-file ...` 的固定调用。
- CLI 安装状态是容器本地实现细节，不能上报或写入 `ac_bot_cli_capability`，也不影响 AgentPass scope。容器重启后状态丢失时正常重探测/重装。

### 已知阻塞/需在编码前确认

1. 给出的 acli bootstrap URL 没有发布 SHA-256。不得填造 hash，也不得在生产路径恢复 `curl | bash`。必须由 artifact release owner 提供不可变版本和 SHA-256；未提供时受支持 profile 的受管 CLI bootstrap 必须显式失败，不可宣称可安全安装。
2. `acli install dataphin`、`acli install di` 给出的命令没有统一安装路径参数，且没有证明实际二进制路径/命令名。`dataphin --version`、`di --version`以及 acli 对 `install_root`/PATH 的正式支持必须在隔离容器实测确认；在确认前不得虚构 `--prefix`、环境变量或软链规则。统一目录契约仅在得到这些事实后落地。
3. AgentPass 已支持 CLI `identity_mode`，但仍需用目标 `openclaw` 与 `claude_code/generalCC` 的真实 CLI 调用链确认其执行面消费该字段；在 E2E 前 caller 是授权记录而非已验证的运行身份。用户已确认一期继续开放该 profile 的 AgentPass caller mutation，验收失败时新增 execution-principal adapter 需求而不误接入 MCP aggregate/IAM。

### 外部系统边界日志

既有业务日志的事件名、消息模板和已有字段（例如 Bootstrap 的 `agent_code`）是兼容性契约；本次仅可新增结构化诊断日志，不得替换、删减或改写既有日志信息。异常正文仍按脱敏要求处理，不得借兼容性要求重新写入 token、Authorization、Cookie 或其他凭据。

| 边界 | 事件 | 必填非敏感字段 | 脱敏/禁止记录 | 验收 |
|---|---|---|---|---|
| 设备 -> Backend Bootstrap | `cli_passport_reconcile_requested/succeeded/failed` | device_id、bot_id、owner_id、engine_type、template_type、profile_id、历史/必选/合并 CLI code、caller/owner 数量、是否更新、耗时、错误类别 | AgentPass token、Authorization、Cookie、完整响应、caller token | 成功、无 profile、AgentPass 失败均有结构化日志 |
| Backend -> AgentPass | `agentpass_cli_scope_update_requested/succeeded/failed` | bot_id、engine_type、MCP code、CLI code 与 identity_mode、关联 ID、状态、耗时、非敏感错误码 | token、认证头、cookie、password、secret、key、credential、session 及嵌套值 | 断言 MCP+CLI 均在请求 scope，失败不出现原始凭据 |
| HTTP CLI call-type | `cli_call_type_update_requested/succeeded/failed` | bot_id、cli_code、目标 call_type、actor_id、锁状态、补偿是否执行、耗时 | 登录 token、IAM/caller token、headers | 权限拒绝、更新成功、AgentPass 失败补偿可查询 |
| DaaS installer | `managed_cli_probe/bootstrap/install/verify` | cli_code、installer、manifest_version/digest、结果、退出码、耗时、是否跳过 | 下载文件内容、任何 token/header/cookie、完整环境变量、未脱敏命令输出 | acli/单 CLI 已安装跳过、安装成功、失败阻断均可定位 |

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
|---|---|---|---|---|
| `CliCapabilityManifestResolver.resolve_profile()` | Backend 配置/领域服务 | 以逻辑 engine + exact template_type 解析 YAML profile 与 catalog；不查询 AgentPass、不写库 | `engine_type, template_type` -> profile/default `CliItem` 或空 | 只读 manifest，拒绝不合法 schema/重复 code |
| `CliPassportScopeReconcileService.reconcile_on_bootstrap()` | Backend 应用服务 | 唯一编排启动时历史 CLI、YAML 必选项、CLI sparse override 与完整 MCP scope；不决定引擎安装 | Bot/device 上下文 -> 收敛 CLI snapshot/是否外部更新 | 查询 AgentPass、读 caller repositories、全量 `update_passport` |
| `CliCallerIdentityService.update_cli_call_type()` | Backend caller identity 领域服务 | 校验 owner/service Bot/锁/已授权 CLI，保存或删除单 CLI override，并原子收敛 MCP+CLI Bot aggregate、AgentPass scope 与补偿 | `bot_id, cli_code, call_type, actor_id, lock_epoch` -> CLI call-type + Bot aggregate result/领域错误 | 读写 `ac_bot_cli_call_config` / `ac_bots`、调用 scope reconciler |
| `extract_cli_items()` 与 `merge_passport_cli_items()` | Passport boundary | 无损标准化 `identity_mode` 并按 cli_code 合并历史/默认；不擅自降级身份 | Passport mapping + default items -> normalized list | 无 I/O；无效 identity fail closed |
| `install_managed_clis.py:install_selected()` | DaaS 启动基础设施 | 根据 manifest 和 Bootstrap code 清单完成 probe/bootstrap/install/verify；不更新 AgentPass、不解析聊天输入 | manifest path、codes file -> exit 0/非零 | 本地文件、固定 argv 子进程、状态文件 |

这些抽象把“授权 scope 的覆盖式一致性”“身份持久化”“容器二进制供应”分开。调用方必须使用 scope reconciler，不能自行组装部分 Passport scope；installer 只信任已解析的配置，不获取或写入授权信息。

### 关键领域模型设计

#### `CliCapabilityManifest` / `CliProfile` / `CliDefinition`

**模型说明**：部署管理的不可变配置值对象。`CliProfile` 决定某逻辑引擎/模板的 Default CLI，`CliDefinition` 定义可被平台安装的 CLI。它们不表达某个 Bot 的安装状态或授权状态。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| `manifest_version` | 非空字符串 | 是 | 同一 Backend/DaaS artifact 一致 | 发布配置 | Bootstrap 与 DaaS 版本一致性检查 |
| `profiles[].match.engine_type` | 枚举 | 是 | 一期仅 `openclaw`/`claude_code` | 发布配置 | 逻辑 engine key，不是 runtime bucket |
| `profiles[].match.template_type` | 字符串/null | 条件必填 | `claude_code` 必须为 `generalCC` | 发布配置 | exact match，不能继承其他 template |
| `default_cli_codes[]` | `cli_code` | 是 | 在 catalog 唯一存在 | 发布配置 | Default 必选授权项 |
| `catalog.<code>.default_identity_mode` | `owner/caller` | 是 | 本期默认 `owner` | 发布配置 | 历史 identity 与 sparse override 均可覆盖它 |
| `install.argv/probe_argv` | 非空 string 数组 | 是 | 禁止 shell/空项/用户占位符 | 发布配置 | 供固定 argv 执行，不是 shell command |

**关系与不变量**：profile 引用的每一个 code 必须在 catalog 中；catalog 可包含未被当前 profile 引用的历史受管 CLI；manifest hash 不一致时设备不可安装。

#### `BotCliCallConfigModel` (`ac_bot_cli_call_config`)

**模型说明**：Bot 对单一 CLI 的稀疏 caller identity 覆盖。生命周期由 active service Bot owner 的 call-type API 管理；它不是 CLI 安装清单或 AgentPass scope 副本，但它与 MCP sparse rows 共同决定 Bot 级 aggregate。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| `id` | bigint | 是 | 自增主键 | 数据库 | ORM 标识 |
| `bot_pk` | bigint | 是 | `ac_bots.id` | Bot 记录 | 归属 Bot |
| `cli_code` | varchar(256) | 是 | 当前 AgentPass CLI scope 中存在 | 调用服务 | 不是 executable 名 |
| `engine_type` | varchar(64) | 是 | 等于当前 active engine | Bot 记录 | engine 改变时旧行不生效 |
| `call_type` | varchar(16) | 是 | 一期只持久化 `caller`；owner 为缺行 | 调用服务 | 线上的 `identity_mode` 为 caller |
| `modifier_id` | varchar(1024) | 是 | 当前可信登录主体 | 安全上下文 | 变更人，不接受请求 body |
| `revision` | bigint | 是 | 单行 CAS 递增 | 数据库 | 同一 lock 下补偿防覆盖；写入时同步推进 Bot aggregate revision |
| `env`, `avernet_tenant` | varchar | 是 | 复用 MCP 表隔离/guard 策略 | 运行环境/tenant guard | 所有读写按当前 env/tenant 过滤 |
| `gmt_create`, `gmt_modified` | timestamp | 是 | DB 默认/更新 | 数据库 | 审计时间 |

**关系与不变量**：唯一键 `(bot_pk, cli_code, engine_type, env)`；只有 `caller` 有行，owner 删除行；行仅能覆盖当前 Passport scope 中的 CLI；不修改任意 MCP row，但与有效 MCP caller row 一起原子决定 `ac_bots.call_type`、`caller_config_revision`。

#### `CliItem`（变更）

**模型说明**：AgentPass CLI resource scope 的传输值对象，由查询、默认补齐、UI 展示和覆盖式更新共享。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| `cli_code` | string | 是 | 非空、scope 内唯一 | AgentPass/YAML | CLI 授权标识 |
| `cli_name` / `cli_desc` | string/null | 否 | 历史值优先 | AgentPass/YAML | Default UI 展示字段 |
| `identity_mode` | `owner/caller` | 是 | 查询值保留；缺失历史值按 owner 规范化；非法值拒绝更新 | AgentPass/local sparse override | 新增的向后兼容响应字段 |

**关系与不变量**：任何非空 CLI scope 都必须带合法 `identity_mode` 后才可进入覆盖式 update；外部历史字段不可因本期收敛被丢弃。

### 文件改动范围

| 仓库/文件路径 | 改动类型 | 改动说明 |
|---|---|---|
| Avernet `src/backend/src/agentclaw/community/configs/cli-capabilities.yaml` | 新增 | canonical YAML manifest（初始 profiles/catalog） |
| Avernet `.../core/*/cli_capabilities*.py`、DI config/module | 新增/修改 | manifest schema、加载校验、profile resolver、scope reconciler 注入 |
| Avernet `.../plugin_api/passport.py`、具体 Passport adapter | 修改 | CLI `identity_mode` 查询/更新无损透传与校验 |
| Avernet `.../core/mcp/services/passport_scope.py`、`sync_service.py`、`bot_runtime_projector.py` | 修改 | 全量 scope builder 共用，移除支持 profile 的硬编码 CLI 合并 |
| Avernet `.../core/bot_management/create_flow.py`、`create_bot_for_others_service.py`、`default_bot_passport_repair_service.py` | 修改 | 保持 generalCC 创建期预注册 aicoding 旧默认值；Bootstrap 复用 scope reconciler 追加 YAML CLI |
| Avernet `.../core/devices/services/device_service_router.py`、devices schemas/router/protocol | 修改 | Bootstrap 调用收敛服务并返回非敏感 CLI manifest/scope 结果 |
| Avernet `.../core/caller_identity/{models,service,contracts,...}.py`、repository protocol/implementation、DI | 修改 | 新 CLI sparse override 模型、持久化、身份服务及同步补偿；CLI 与 MCP 一起收敛 Bot aggregate 语义 |
| Avernet `.../core/caller_identity/sql/<new>_cli_call_config.sql` | 新增 | 创建 `ac_bot_cli_call_config`、唯一/查询索引、tenant 字段 |
| Avernet `.../adapters/http/openapi_v1/caller_identity/{router,schemas}.py` | 修改 | 在既有 caller router 平铺 CLI call-type endpoint |
| Avernet `.../core/skill_center/services/skill_set_management_service.py` 及 skillsets schemas | 修改 | Default CLI response 透传 identity_mode；非默认 set 永远为空 CLI |
| Avernet `tests/community/...` | 新增/修改 | YAML、scope、Bootstrap、caller API/repository、UI contract 覆盖 |
| DaaS `confs/cli-capabilities.yaml` 或构建生成路径 | 新增 | 与 canonical manifest 同 hash 的设备可读 artifact |
| DaaS `bootstrapping/install_managed_clis.py` | 新增 | 安全的通用 CLI installer |
| DaaS `bootstrapping/bootstrap_device_auth.sh`、`bootstrapping/start_service.sh` | 修改 | 解析新增 Bootstrap 字段，在 join 后、引擎启动前固定调用 installer |
| DaaS `tests/test_managed_cli_installer.py`、`tests/test_start_service_*.py` | 新增/修改 | probe/skip/install/fail/version mismatch、两个引擎挂点测试 |

### 允许与禁止改动边界

**允许新增点**：Avernet Passport scope/Bootstrap/Caller identity/Default SkillSet BFF；DaaS Bootstrap 后安装阶段及专用 installer；两仓的受控 YAML artifact 与测试。

**禁止触碰点**：不改 OpenClaw/Claude Code/relay 的通用命令执行器、MCP caller IAM 换签的协议本身、非 Default SkillSet membership、MCP 的既有数据表或不相关 engine 的 CLI 默认值；不创建 `ac_bot_cli_capability`；不以删除历史 AgentPass CLI 作为“收敛”；不在 router adapter 组装 scope 或实现业务校验。

### 验收标准

- [ ] `openclaw` 与精确 `claude_code/generalCC` Bootstrap 均补齐 `dataphin`、`deepinsight-cli`；`claude_code/normalCC`、其它 template 和其它 engine 不补齐。
- [ ] 历史 AgentPass CLI 的 code/name/desc/identity_mode 保留；历史同 code 优先，YAML 只补缺项；本地 caller override 优先于历史 identity，未配置时默认 owner。
- [ ] 每一次需要更新的 Bootstrap scope 都包含完整 MCP `mcp_items` identity 与完整 CLI `cli_items` identity；任一读取失败不发部分覆盖。
- [ ] Default 能力集只展示 AgentPass CLI 且包含 `identity_mode`；非 Default Set 无 CLI；不新增非 Default CLI API。
- [ ] CLI caller 设置只作用于对应 `cli_code` 的 Passport identity，但与有效 MCP caller 一起原子更新 Bot aggregate；最后一个 caller 不可切回 owner，仍有其它 caller 时允许删除 sparse row；无权、非 active service Bot、无锁或未知 CLI 均失败。
- [ ] `ac_bot_cli_call_config` 具备 env/tenant 隔离和唯一约束；AgentPass 写失败后 sparse 覆盖恢复前态。
- [ ] `CliItem` 从查询到 update 到 UI 不丢失 `identity_mode`；AgentPass 的 CLI caller/owner 执行消费在两个目标 engine 完成 E2E 验收前，不得将运行时 token 注入表述为已验证。用户已确认本期 API 仍交付 AgentPass authorization configuration，不因该未验证项自行拒绝已授权 CLI 的 caller mutation。
- [ ] CLI caller/owner mutation 仅允许精确 profile `openclaw` 与 `claude_code/generalCC`；其它 engine/template 即使历史 AgentPass 中存在 CLI 也按只读配置拒绝。
- [ ] `acli` 已存在时不 bootstrap；每个 CLI probe 成功时不安装；缺失时按 `acli install dataphin`、`acli install di` 安装并复检；失败阻断本次启动。
- [ ] manifest SHA/version 不一致、未知 catalog code、错误 hash、非法 YAML argv、安装/探测失败均可预测地失败，不执行 shell 注入。
- [ ] 安装状态仅在 `${install_root}/install-state.json`；无新增 Bot CLI capability/install 状态表。
- [ ] 新增/变更外部边界日志覆盖请求、成功、失败，且 token、Authorization、Cookie、密码、secret、key、credential、session 不以明文出现在任意嵌套日志。
- [ ] 本次变更改动文件单测行覆盖率 > 90%，使用 `pytest --cov --cov-report=term-missing` 实测。

## Review Spec

### 关注点

- YAML profile 必须按逻辑 engine/template 精确匹配，不能沿用 Claude Code 到 aicoding 的默认能力分桶。
- 所有 AgentPass resourceManifest writer 必须带完整 MCP + CLI identity scope，且只经共享构建逻辑。
- CLI caller/owner 的持久化、AgentPass identity 和实际 engine 消费三者必须有明确闭环；不得半实现为 UI 字段。
- 安装器必须以受控 argv 执行并在现有 `eval` 并行框架之外运行。

### 检查项

- [ ] `CliItem`/query extractor/update serializer/response schema 均支持并验证 `identity_mode in {owner, caller}`。
- [ ] 历史 CLI 字段和 caller identity 在启动/MCP 同步/删除 CLI/runtime projection/repair 之后仍保持；没有硬编码 `get_default_cli_items()` 从旁路重写支持 profile。
- [ ] CLI sparse override 优先级、owner 删除、事务/锁/CAS 补偿、env/tenant 过滤均符合模型不变量。
- [ ] CLI 不纳入 `ac_bots.call_type`、`caller_config_revision`、MCP IAM 换签；若需 caller runtime token 的 engine 不会开放伪生效的 caller 开关。
- [ ] CLI call-type endpoint 平铺在已有 caller router；router 只做 HTTP/登录态/响应映射，授权、Bot 查找、scope 编排均在 service。
- [ ] Bootstrap 只使用受验证的 Bot/device/owner 关系，CLI code 不接受设备、前端或模型任意指定。
- [ ] manifest 的 digest、URL hash、catalog/argv 验证与安装路径事实明确实现；不得使用 `curl | bash`、`eval`、`sh -c` 或伪造 acli `--prefix`。
- [ ] 安装器位于 Bootstrap 后、引擎启动前，覆盖 OpenClaw 和 Claude Code；不放入 `run_task_async`。
- [ ] 新增/修改的外部调用日志满足 Spec 且递归脱敏凭据。
- [ ] 关键方法职责边界、输入输出、错误处理和副作用与「关键方法抽象」一致。
- [ ] 领域模型字段、唯一约束与 owner-missing-row 不变量均按设计实现。
- [ ] 本次变更单测行覆盖率 > 90%（改动文件实测，未达标判 REJECT）。

### 不可接受的模式

- 只在 UI 或 `ac_bot_cli_call_config` 写 caller 而未完整回写 AgentPass scope：配置与授权分离，无法证明生效。
- 复用 MCP Bot aggregate/IAM caller 链路管理 CLI：会把单 CLI 的身份变更扩大成全 Bot MCP 身份变更，并禁止合法回退。
- 把 YAML 默认项当作全局覆盖，移除历史 CLI 或以 YAML metadata 覆盖历史 AgentPass item：违反“历史保留、默认补齐”。
- 用 shell string、`eval`、`curl | bash` 或用户插值执行安装命令：安装 manifest 将成为任意命令面。
- 未使用的 import / 局部变量（IDE/linter ACI 告警）。
- 因本次改动残留的孤儿代码（删逻辑后未清理的 import / 变量 / 函数）。
- Python 风格违规：`:` 前有空格（`whitespace before ':'`）、block comment 未以 `# ` 开头。

## QA Spec

### 前置条件

- AgentPass fake/测试环境支持 CLI `identity_mode` 回显和覆盖式 resourceManifest 断言。
- 提供 acli bootstrap artifact 的真实不可变 SHA-256，并在隔离容器确认 acli 安装目录、`dataphin`/`di` executable 与 `--version` 探测命令。
- 可启动的 `openclaw`、`claude_code/generalCC` 测试 Bot，以及一个不命中 profile 的 Claude template Bot。

### 测试用例

| 编号 | 用例名称 | 操作步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | OpenClaw 启动补齐默认 CLI | 准备无 CLI 历史的 OpenClaw Bot；调用 bootstrap-auth | AgentPass 收到完整 MCP + `dataphin/deepinsight-cli`，二者 identity 为 owner，返回 manifest 信息 |
| TC-02 | Claude generalCC 精确匹配 | 创建并启动 `claude_code/generalCC`，并分别启动 normalCC/其它模板 | 创建期保留旧 aicoding 9 项；仅 generalCC 在其基础上补齐两项 YAML CLI；其它模板不新增 YAML CLI |
| TC-03 | 历史 CLI/身份保留 | AgentPass 预置自定义 CLI caller 与 `dataphin` caller；启动匹配 profile | 自定义项存在，`dataphin` 文案/identity 保留，缺失 `deepinsight-cli` 被补齐 |
| TC-04 | sparse override 优先 | 为 `dataphin` 写 caller override，AgentPass 历史为 owner；重启 | 全量 scope 中 dataphin 为 caller，其他历史 CLI 不变 |
| TC-05 | CLI 切换与回退 | owner 在有/无锁两种条件下 caller->owner；模拟 AgentPass 失败 | caller 创建行并同步；owner 删除行；无锁/无权/未知 code 拒绝；失败补偿原状态 |
| TC-06 | MCP scope 不回退 | MCP 有 caller、CLI 收敛或切换 | AgentPass update 的 MCP identity 仍为 caller，Bot `call_type` 未因 CLI 改变 |
| TC-07 | Default UI identity | 查询 `/api/skillsets/resources` | Default Set 返回 CLI `identity_mode`；其它 Set CLI 为空 |
| TC-08 | acli/CLI 幂等安装 | 预置 acli/两个二进制和匹配 state；运行 installer | 只 probe，无 bootstrap/install；状态保持 |
| TC-09 | 安装与失败阻断 | 分别缺 acli、缺 dataphin、令 di probe 失败 | 按顺序 bootstrap/install/verify；最终失败写 FAILED 并未启动引擎 |
| TC-10 | manifest 与日志安全 | version/hash 不符、非法 argv、Bootstrap/AgentPass/install 失败 | 拒绝执行；日志含 code/status/duration，不含 token、Cookie、Authorization、下载内容 |
| TC-11 | caller 执行消费验证 | 在每个允许 caller 的目标 engine 发起可观测 CLI 调用 | 证明 AgentPass 按 CLI `identity_mode` 使用 caller/owner；无法证明则 API 禁止 caller |

## Ship Spec

### 部署目标环境

- [ ] 线下环境：先以 fake AgentPass + 隔离安装目录跑 Backend/DaaS 单测和启动冒烟。
- [ ] 预发环境：使用真实 AgentPass contract 与真实 artifact SHA，分别验证 OpenClaw 与 Claude Code generalCC。

### 分支策略

- Avernet 开发分支：`feat/cli-default-install-identity`（worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity`）
- DaaS 开发分支：`feat/cli-default-install-identity`（worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/daas-script_worktree/cli-default-install-identity`）
- 目标分支：各自 `origin/dev`。
- Avernet 源码仅通过 GitHub `inclusionAI/Avernet` rebase/push/PR；不得向镜像 remote 提交。两仓 manifest artifact 的 version/SHA 必须在 PR 描述与交付验证中对应。

### 回滚方案

1. 回滚 Avernet 与 DaaS 到同时不读取该 manifest 的已验证版本；不要单独回滚一仓造成 Bootstrap version/digest 不匹配。
2. 已写入 AgentPass 的 CLI scope 不自动删除历史或新默认 CLI。需要业务回滚授权时，使用共享 reconciler 以完整 MCP+CLI scope 做显式回写，不能只传 CLI list。
3. 容器本地安装目录/状态文件可随容器生命周期清理；不操作数据库以外的授权状态。稀疏 CLI caller row 按 owner 回退接口删除，不影响 MCP caller 表。
