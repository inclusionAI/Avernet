# 模板变更驱动的 MCP/CLI 授权知情同意（简化版）

GitHub issue: 待建（与本 spec 同步提交）。

## Summary

模板有版本更新、新版本会向默认能力新增 MCP/CLI。容器重启时授权不会自动跟随——现有
授权只在 SkillSet 显式变更时触发（`refresh_mcp_scope`）。结果：即便 bot 用到了新模板
配置，新默认 MCP/CLI 也处于**未授权**，用户既不知情也无法选择。

本方案把这个静默升级变成一次**知情同意**，只有「同意 / 一键添加」语义，**无拒绝、无状态机**：

- **diff 由前端直连模板市场取**：比较「**bot 当前配置**」与「**最新版本模板**」的差别
  （added/removed mcp/cli）。**OCB 后端不算 diff。** diff 入参待与模板市场同学确认
  （见 §diff 接口契约）。
- **确认重启**（`POST /restart` 带 `confirmed_template_update=true`）是唯一的「升级 + 授权」
  动作，Path A / Path B 都复用它：
  - 后端按 `template_uid` 向模板市场**拉取最新版本模板配置**，注入 `bot.template_config`
    （复用 `apply_restart_extra_configs`→`update_template`，但 incoming 不再来自请求 body，
    而来自「拉取最新」）。
  - 注入后调 `refresh_mcp_scope` **自动授权**新增 MCP/CLI。
- **普通重启**（不带参数）：既不升级配置、也不授权，语义不变（代码现状即如此）。
- **弹窗条件**：仅当 **diff 有差异** **且** **首次进入页面** 同时满足才弹；否则不弹。

授权链路复用现有 `MCPSyncService.refresh_mcp_scope`（对 MCP 与 CLI 都写 passport，CLI 默认项
从当前 `bot.template_config` 派生），不新增 passport 协议、不新增状态字段、不写排除表、
不存在「拒绝」分支。

## 代码现状（方案立足的事实基础）

经核实，OCB 后端**不会**在普通重启时把 bot 升级到模板最新版本：

- `bot.template_config` 只在创建、用户 `update_bot`、或上游主动传
  `extra_configs.template_config` 时变化。
- `restart_bot` 的 `extra_configs` 默认 `None`（`bot_service.py:4098`），
  `restart_scheduler` 也不传；`apply_restart_extra_configs`（`strategy.py:424`）仅当
  `extra_configs.get("template_config")` 存在且 `incoming_version > stored_version` 才
  `update_template`。无传入则什么都不做。
- 重启 / 设备重分配路径（`_allocate_device_async`、`start_bot`、`baas_device_service`）读的都是
  `get_template_config(bot_id)`=**当前已存配置**；`_start_publish_polling` 成功回调不碰
  `template_config`。全仓**无**按 `template_uid` 拉取最新版本配置回写的调用。

因此「把 bot 升到最新模板配置」在 OCB 现状里**不存在**，必须由本方案在「确认重启」路径里
**新增**。普通重启保持不升级（代码现状），这正是 Path B 能在「重启后」仍取到非空 diff 的
前提——也是与用户新 diff 定义（bot 当前配置 vs 最新模板）自洽的关键。

## Motivation

现状缺口：重启不升级配置、即便升级授权也不跟随；新建时授权发生在 `_apply_passport`
（`create_flow.py:191`，mcp_codes + `get_default_cli_items(...template_config)` 一次性写入
passport），重启路径无等价一步。用户对「新模板带来哪些新 MCP/CLI」无感知、无选择权。

## 目标 / 非目标

目标：

- 模板版本变更导致默认 MCP/CLI 变化时，用户能**知情**（看到 diff）、能**一键同意并升级+授权**。
- 同意 → bot 升级到最新模板配置，新增 MCP/CLI 自动获得 AgentPass 授权。
- diff 由模板市场提供，前端直连；OCB 后端不算 diff。
- 「拉取最新配置 + 授权」仅发生在「确认重启」，复用现有注入与授权链路。
- 不引入状态机、不引入拒绝语义、不新增持久字段。

非目标：

- 不在 OCB 后端实现/缓存 diff（仅前端取 diff）。
- 不实现「拒绝新增」——只有「同意并升级+授权」。（「拒绝/永久排除」是独立增强，不在本期。）
- 不改变模板发布与版本机制。
- 普通重启语义不变（不升级、不授权）。

## 整体架构

```
                 ┌─────────────── 模板市场 ───────────────┐
   ① diff 接口     │  比较：bot 当前配置 vs 最新版本模板        │
   (前端直连)  ──▶ │  出参：added/removed mcp、added/removed cli │
                 │  入参：待与模板市场同学确认（见契约）          │
                 └──────────────────────────────────────────┘
                              ▲
                 前端用「定位 bot 当前配置」的信息调（具体入参待确认）
                              │
   ┌──────────────────────────┴────────────────────────────────────────┐
   │ 前端（bot 页面）                                                      │
   │  - 进页面：调详情接口拿 template_uid/template_version；调模板市场 diff  │
   │  - 弹窗条件：diff 有差异 且 首次进入页面（二者同时满足）才弹             │
   │      Path A：点「允许变更并重启」│ Path B：弹「是否一键添加」            │
   │      ——两者动作相同：restart{confirmed_template_update:true}          │
   │  - diff 无差异 / 非首次进入 → 不弹                                      │
   └─────────────────────────────────┬────────────────────────────────────┘
                                     │ restart{confirmed_template_update:true}
                                     ▼
   ┌───────────────────── OCB 后端（不算 diff、无状态机）──────────────────┐
   │  restart 解析 confirmed_template_update：                              │
   │   false（默认）→ 普通重启：不升级、不授权（现状）                         │
   │   true → 确认重启：                                                     │
   │     1) 按 template_uid 向模板市场拉取最新版本模板配置                     │
   │     2) 注入 bot.template_config（复用 update_template）                 │
   │     3) refresh_mcp_scope 自动授权（MCP+CLI）                            │
   │  详情接口下发 template_uid/template_version（已有，无需新增字段）        │
   └────────────────────────────────────────────────────────────────────────┘
```

关键边界：**后端不算 diff、不存状态**。后端在「确认重启」里做三件事——拉取最新配置、注入、
授权。授权正确性来自「注入后 `get_default_cli_items` 读到新配置」，与 diff 无关。

## diff 接口契约（模板市场提供，OCB 仅消费约定）

- 调用方：前端。
- **比较语义**：「**bot 当前配置**」 vs 「**最新版本模板**」的差别（added/removed mcp/cli）。
- **入参：待与模板市场同学确认**（不写死）。候选：足以定位「bot 当前配置」的标识，如
  `{ template_uid, template_version }` 或 `{ template_uid, template_version_id }`
  （均来自 `bot.template_config`，详情接口已透出）。最终以模板市场定义为准。
- 出参（建议结构，以模板市场为准）：
  ```jsonc
  {
    "has_change": true,
    "added_mcp":   [ { "server_code": "...", "name": "..." } ],
    "removed_mcp": [ { "server_code": "...", "name": "..." } ],
    "added_cli":   [ { "cli_code": "...", "name": "..." } ],
    "removed_cli": [ { "cli_code": "...", "name": "..." } ]
  }
  ```
- `has_change=false` 时前端不弹窗。
- 两条路径入参相同（都定位「bot 当前配置」）：
  - **Path A**（重启前）：bot 当前配置 = 旧版本 → diff(旧 vs 最新) 有差异 → 提示。
  - **Path B**（重启后）：因普通重启不升级配置，bot 当前配置**仍是旧版本** →
    diff(旧 vs 最新) **仍有差异** → 首次进入页面时弹窗。
  - 这与"普通重启不升级"的代码现状自洽，避免"重启后入参变空"的死结。

## 拉取最新配置接口契约（模板市场提供，OCB 后端调用）⚠️需确认

「确认重启」需要 OCB **主动拉取最新版本模板配置**注入 bot。这是新增的后端↔模板市场调用，
契约**待与模板市场同学确认**：

- 调用方：OCB 后端（确认重启路径内）。
- 入参：定位「要拉哪个模板的最新版本」的标识，候选 `{ template_uid }`（并带上
  `template_type`/`engine_type` 辅助），最终以模板市场为准。
- 出参：最新版本的完整模板配置（含 `template_uid`/`template_version`/`template_version_id`
  及默认 MCP/CLI 等），结构需能直接喂给 `template_service.update_template` 与
  `get_default_cli_items(ext_info={template_config})`。
- 鉴权/网络：沿用现有 OCB↔模板市场调用的鉴权方式（若无既有调用，需新增 OCB 侧 client）。

> 注：与 diff 接口是两条不同的模板市场接口——diff 服务前端展示，本接口服务后端注入。
> 两者入参都待确认。若模板市场能合并为「一个接口同时返回 diff + 最新配置」，可简化前端与
> 后端，但默认按两条接口设计。

## 数据模型

**无新增状态字段、无状态机。** 不写 `bot.ext.template_update` 之类。

- `confirmed_template_update`（restart 请求 body 的可选 bool，默认 false）：在重启链路内消费，
  **不持久化**、不入 bot.ext。
- `template_service.update_template` 写入最新 `template_config`（复用现有表 `ac_templates.ext`）。
- `refresh_mcp_scope` 调用（现有能力，无 schema 变更）。

## 接口设计

### 1) 详情接口（已有，无改动）

已透出 `template_uid`/`template_version`/`template_version_id`（来自 `bot.template_config`，
`capabilities.py:12-14`）。前端据此取 diff。**不新增字段、不新增状态。**

### 2) 重启接口 `POST /api/bots/{bot_id}/restart`（现有，增一个入参 + 一个分支）

- 位置：`adapters/http/bot_management/router.py:2627`；openapi 对应
  `adapters/http/openapi_v1/bots/router.py:600`。
- 新增可选 body 字段：`confirmed_template_update: bool`（默认 `false`，向后兼容）。
- 后端逻辑（`restart_bot`→`bot_service.restart_bot`）：
  1. 现有生命周期校验照旧。
  2. `confirmed_template_update==false`（默认）→ **普通重启，不升级、不授权**（现状，不动）。
  3. `confirmed_template_update==true` → **确认重启**：
     a. 按 `template_uid` 调模板市场**拉取最新版本模板配置**。
     b. 复用 `apply_restart_extra_configs` 的注入判定（`incoming_version > stored_version` 才
        `update_template`；相等或更旧则跳过注入，但仍可继续步骤 c 做幂等授权刷新）。
        - 复用方式：把拉取到的最新配置构造成 `extra_configs={"template_config": latest}` 传入，
          或新增一个「按 template_uid 注入最新」的 strategy 方法。实现择优（见 plan.md）。
     c. 注入完成后调 `refresh_mcp_scope` **授权**新增 MCP/CLI。
  4. 拉取或注入失败：日志告警 + 返回值上抛（**不降级为静默授权缺失**；用户可重试，幂等无害）。
- 重启沿用现有幂等锁 `ac_bot_restart_lock` 与异步 202 语义。
- `confirmed_template_update` 不写 bot.ext、不透出。

### 不新增的接口

- 不新增 `confirm`/`authorize` 类接口：Path A / Path B 确认后的动作都是「再调一次
  restart 带 `confirmed_template_update=true`」，复用同一路径。

## 授权链路复用与落点

复用核心：`MCPSyncService.refresh_mcp_scope`（`mcp/services/sync_service.py:325`）。对 **MCP 与
CLI 都写 passport**：

- MCP：`collect_bot_active_mcps` → `_declare_mcp_scope`（设备白名单）→ `_update_passport` 写 mcp_codes。
- CLI：`_update_passport`（`sync_service.py:850`）读 `bot.template_config`，调
  `get_default_cli_items(engine_type, template_type, ext_info={template_config})`（`_defaults.py:295`）
  取**当前模板默认 CLI**，`_merge_cli_items`（`sync_service.py:43`）去重合并后覆盖写 passport。

故「**授权（MCP+CLI）**」= **注入最新 template_config 之后调一次 `refresh_mcp_scope`**。新默认
CLI 因 `get_default_cli_items` 读到新配置而被自动补入。无需新增授权代码。

授权时机（与创建对齐）：

- 创建：`create_bot_with_authorization`（`create_flow.py:307`）先 `_apply_passport`（L357）再
  `create_bot`（L413）。
- 重启：passport 身份已存在（`agent_code`），不走 apply，只走 `update_passport`。故
  **确认重启授权 = 注入最新配置之后调 `refresh_mcp_scope`**。
- 必须保证「拉取并注入最新配置」成功之后才 `refresh_mcp_scope`，否则读旧配置、漏授权新 CLI。

落点（推荐 R1）：在 `bot_service.restart_bot` 的确认分支内，按顺序执行「拉取→注入→授权」，
注入复用 `apply_restart_extra_configs`，授权调用 `refresh_mcp_scope`。MCPSyncService 的注入由
DI 提供；若 `restart_bot` 当前不持有 MCPSyncService，按现有 factory 模式注入（见 plan.md）。

## Path A 流程（重启前确认 → 升级 + 授权）

```
[用户进 bot 页面]
   │ 前端调详情接口 → 拿 template_uid/template_version（=当前旧版本 v_old）
   │ 前端调模板市场 diff(定位 bot 当前配置) → has_change=true
   ▼
[展示「新版本模板更新内容：added/removed MCP/CLI」]
   │ 用户点「允许变更并重启」
   ▼
[前端 POST /restart {confirmed_template_update:true}]
   │ 后端：按 template_uid 拉取最新配置 → 注入 bot.template_config → refresh_mcp_scope 授权
   ▼
[重启完成；新增 mcp+cli 已授权] → 前端轮询 status 完成即结束
[拉取/注入/授权失败：日志告警 + 上抛；用户可重试]
```

## Path B 流程（重启后弹窗 → 一键添加）

```
[用户此前未确认、普通重启了该 bot（默认 confirmed=false）]
   │ 后端：普通重启，不升级配置、不授权（现状）；bot.template_config 仍为 v_old
   ▼
[重启完成，bot 仍是旧版本 v_old，新增 mcp/cli 未授权]
   │
[用户首次进入页面]
   │ 前端调模板市场 diff(定位 bot 当前配置=v_old) → has_change=true（旧 vs 最新 有差异）
   │ 满足「diff 有差异 且 首次进入页面」→ 弹窗
   ▼
[弹窗「本次重启更新内容：added MCP/CLI，是否一键添加？」]
   │ 用户点「一键添加」
   ▼
[前端 POST /restart {confirmed_template_update:true}]
   │ 后端：按 template_uid 拉取最新配置 → 注入 → refresh_mcp_scope 授权（同 Path A）
   ▼
[bot 升级到最新 + 新增 mcp/cli 已授权]
[diff 无差异 / 非首次进入 → 不弹窗]
```

> 重启后若用户一直不点「一键添加」：bot 保持旧配置、新 MCP/CLI 不授权（可接受——用户知情后
> 主动放弃，非静默脱节）。下次进页面若仍「首次针对该变更」可再弹；前端用「已对该 bot 展示
> 过该版本变更」标记避免重复打扰（前端实现细节，OCB 不参与）。

## Path B 待澄清项（前端/产品，非后端阻塞）

1. **「首次进入页面」判定**：前端按 bot_id + 当前 template_version 维护「是否已弹过」标记
   （如 localStorage），避免重复弹。OCB 后端不参与。
2. **diff 入参最终定义**：见 §diff 接口契约，待与模板市场同学确认。
3. **拉取配置接口入参/出参**：见 §拉取最新配置接口契约，待确认。

## 边界与约束

- **授权与 diff 解耦**：后端不算 diff；授权正确性来自「注入最新配置 + get_default_cli_items 派生」。
- **无状态、无拒绝**：不写 bot.ext、不引入 status；不存在 reject/排除表写入。
- **fail-closed（CLI）**：沿用 `get_default_cli_items` 未知桶返回空、`_merge_cli_items` 仅合并
  已知默认、`query_passport_clis` 失败则 `_update_passport` 中止（不写半截快照）。
- **幂等**：`refresh_mcp_scope` 多次调用无害；`restart` 沿用 `ac_bot_restart_lock`；确认重启对
  「已是最新版本」的 bot 调用，注入会被 `incoming<=stored` 跳过，授权刷新幂等无副作用。
- **拉取最新配置失败不静默**：确认重启里拉取/注入失败必须上抛，不可悄悄继续成「重启了但没
  升级没授权」的半成品态。
- **多引擎**：仅 `TEMPLATE_CONFIG_CONSUMING_ENGINES`（`apply_restart_extra_configs` 已限定）
  参与注入与授权；其他引擎确认重启退化为普通重启。
- **移除项**：模板移除的 MCP/CLI 不需特殊处理——下次 `refresh_mcp_scope` 自然不再包含。

## 参考（代码坐标）

- 重启端点：`adapters/http/bot_management/router.py:2627`（`restart_bot`）、
  `adapters/http/openapi_v1/bots/router.py:600`。
- 模板版本字段：`core/bot_management/capabilities.py:12-14`。
- restart 链路：`core/bot_management/services/bot_service.py:4098`（`restart_bot` 签名/默认
  `extra_configs=None`）、`:4182`（`apply_restart_extra_configs` 调用点）。
- 版本比较与注入：`core/bot_management/engines/aicoding/strategy.py:412`（`_template_version_id`）、
  `:424`（`apply_restart_extra_configs`，`:449` `update_template`）。
- 模板读写：`core/bot_management/services/template_service.py:232`（`get_template_config`）、
  `:301`（`update_template`）。
- 创建授权：`core/bot_management/create_flow.py:307`（`create_bot_with_authorization`）、
  `:191`（`_apply_passport`）、`:357`（apply 调用）。
- 授权刷新（MCP+CLI）：`core/mcp/services/sync_service.py:325`（`refresh_mcp_scope`）、
  `:850`（`_update_passport`）、`:43`（`_merge_cli_items`）。
- 默认 CLI/MCP：`core/mcp/services/_defaults.py:295`（`get_default_cli_items`）、
  `:199`（`get_default_mcp_servers`）。
- 重启服务：`core/desktop_bot/services/desktop_bot_service.py:781`（`restart`）、
  `_start_publish_polling`、幂等锁 `ac_bot_restart_lock`。
- passport 协议：`plugin_api/passport.py`（`update_passport` 覆盖写 resourceManifest）。
