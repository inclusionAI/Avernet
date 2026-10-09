# 实施计划：模板变更驱动的 MCP/CLI 授权知情同意（简化版）

> 配套 spec.md。聚焦"如何落地"。

## 核心动作（唯一新增能力）

「确认重启」= `POST /restart` 带 `confirmed_template_update=true` 时，按顺序执行：
1. 按 `template_uid` 向模板市场拉取最新版本模板配置；
2. 注入 `bot.template_config`（复用 `apply_restart_extra_configs`/`update_template`）；
3. `refresh_mcp_scope` 授权（MCP+CLI）。

普通重启（默认 false）不升级、不授权，语义不变（代码现状即如此）。

## 复用映射

| 能力 | 复用对象 | 位置 | 说明 |
|---|---|---|---|
| 版本比较/注入 | `apply_restart_extra_configs` + `_template_version_id` | `strategy.py:424`/`:412` | 把拉取到的最新配置构造成 `extra_configs={"template_config":latest}` 传入，复用 `incoming>stored` 判定与 `update_template` |
| 写 template_config | `template_service.update_template` | `template_service.py:301` | 现有，不改 |
| 授权 MCP+CLI | `MCPSyncService.refresh_mcp_scope` | `sync_service.py:325` | 对 MCP 与 CLI 都写 passport；CLI 默认来自注入后的 `bot.template_config` |
| CLI 合并/默认 | `_merge_cli_items` + `get_default_cli_items` | `sync_service.py:43` / `_defaults.py:295` | fail-closed；注入后读取即授权新 CLI |
| 创建授权时机（参照） | `create_bot_with_authorization`→`_apply_passport`→`create_bot` | `create_flow.py:307/191/357` | 重启不走 apply，走 `update_passport` |
| 重启幂等 | `ac_bot_restart_lock` | restart 链路 | 沿用 |
| 详情透出版本 | `template_uid`/`template_version`/`template_version_id` | `capabilities.py:12-14` | 已有，无需改 |

## 改动面（2 处，均不碰 diff、无状态机）

### 改动①　重启端点增 `confirmed_template_update`
- `adapters/http/bot_management/router.py:2627` `restart_bot`：从 body 解析
  `confirmed_template_update`（默认 false，向后兼容），透传进 `bot_service.restart_bot`。
- `openapi_v1/bots/router.py:600` 同步加同名字段（可选）。

### 改动②　`restart_bot` 的确认分支：拉取→注入→授权
- `core/bot_management/services/bot_service.py:4098` `restart_bot`：
  - `confirmed_template_update==false` → 现有普通重启路径，不动。
  - `confirmed_template_update==true`：
    a. **拉取最新配置**：新增「调用模板市场按 `template_uid` 取最新版本配置」的 client/method。
       - 入参/出参契约见 spec §拉取最新配置接口契约（待与模板市场同学确认）。
       - 出参须能直接喂 `update_template` 与 `get_default_cli_items(ext_info={template_config})`。
    b. **注入**：把拉取到的配置构造成 `extra_configs={"template_config": latest}`，复用现有
       `apply_restart_extra_configs`（`:4182`）→ 命中 `incoming>stored` 才 `update_template`；
       已是最新则跳过注入。
    c. **授权**：注入后调 `refresh_mcp_scope`。需把 `MCPSyncService`（或其 factory）注入到
       `restart_bot` 可达处（沿用现有 `skill_set_factory`/DI 模式）。
    d. **失败不静默**：拉取/注入/授权任一失败 → 日志告警 + 上抛，不降级为"重启但没升级没授权"。
- `confirmed_template_update` 不写 bot.ext、不透出。

### 不再做的事（相对旧版）
- 不新增 `template-update/confirm` 接口（Path A/B 确认动作都是 restart 带参）。
- 不写 `bot.ext.template_update` 状态字段、不引入状态机。
- 不写 MCP 排除表、不实现 CLI 拒绝（无拒绝语义）。

## 前端契约（OCB 仅保证下发/入参）
- 详情接口下发：`template_uid`/`template_version`/`template_version_id`（已有）。
- restart 入参 `confirmed_template_update: bool`（可选，默认 false）。
- diff 由前端直连模板市场取；弹窗条件「diff 有差异 且 首次进入页面」（前端实现，OCB 不参与）。

## 分阶段
1. **P1 模板市场接口对齐**：与模板市场同学确认 ①diff 入参；②「按 template_uid 拉最新配置」
   入参/出参/鉴权。若无现成"拉取最新配置"接口，需模板市场新增。
2. **P2 确认重启分支**：改动①+②-a/b（拉取+注入）；先不含授权，端到端跑通"确认重启→bot 升级
   到最新配置"。单测覆盖拉取失败上抛、已是最新跳过注入、版本比较。
3. **P3 授权接入**：②-c 注入后 `refresh_mcp_scope`；注入 MCPSyncService 注入；失败上抛。
   端到端：确认重启后新增 mcp+cli 已授权；普通重启不升级不授权（验证现状未被破坏）。
4. **P4 openapi surface + 边界**：openapi restart 字段、admission（`admission.py`）、错误码、
   仅 `TEMPLATE_CONFIG_CONSUMING_ENGINES` 触发；评审守住"后端不算 diff"。

## 风险与对策
- **拉取最新配置依赖外部接口**：P1 前无法实现 P2；优先确认契约。若模板市场无现成接口，需
  其新增，工期依赖外部。
- **授权时序**：必须注入成功后才 `refresh_mcp_scope`，否则读旧配置漏授权新 CLI。代码注释固化。
- **拉取/注入失败静默化**：严禁"重启成功但配置没升级、授权没做"。失败必须上抛、让用户可重试。
- **幂等**：确认重启对已是最新版本的 bot，注入被 `incoming<=stored` 跳过，授权刷新幂等无副作用。
- **多引擎**：非 `TEMPLATE_CONFIG_CONSUMING_ENGINES` 确认重启退化为普通重启，不误拉取/授权。
- **diff 与授权解耦**：评审守住"授权路径不读/不算 diff"。

## 开放项（需模板市场/前端确认）
- diff 接口入参（待与模板市场同学确认）。
- 「按 template_uid 拉取最新配置」接口入参/出参/鉴权（待确认；若无现成需新增）。
- 「首次进入页面」判定（前端实现）。
