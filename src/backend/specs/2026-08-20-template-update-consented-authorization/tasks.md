# 任务清单：模板变更驱动的 MCP/CLI 授权知情同意（简化版）

> 配套 spec.md / plan.md。`[ ]`待办 `[~]`进行中 `[x]`完成。

## P0　接口契约对齐（外部依赖，优先）
- [ ] 与模板市场同学确认 **diff 接口入参**（比较「bot 当前配置 vs 最新版本模板」）。
- [ ] 与模板市场同学确认 **「按 template_uid 拉取最新版本模板配置」接口**：入参/出参/鉴权。
      出参须能直接喂 `template_service.update_template` 与
      `get_default_cli_items(ext_info={template_config})`。
- [ ] 若模板市场无「拉取最新配置」现成接口 → 推动其新增，出接口契约。

## P1　重启端点增参（改动①）
- [ ] `restart_bot`（`router.py:2627`）从 body 解析 `confirmed_template_update`（默认 false），
      透传 `bot_service.restart_bot`。
- [ ] `openapi_v1/bots/router.py:600` 同步加可选字段。
- [ ] `confirmed_template_update` 不写 bot.ext、不透出。单测：默认 false 向后兼容。

## P2　确认重启：拉取 + 注入（改动②-a/b）
- [ ] 新增「按 template_uid 调模板市场拉取最新版本配置」的 client/method（契约按 P0）。
- [ ] `bot_service.restart_bot` 确认分支：拉取最新配置 → 构造
      `extra_configs={"template_config": latest}` → 复用 `apply_restart_extra_configs`(`:4182`)
      注入（命中 `incoming>stored` 才 `update_template`）。
- [ ] 拉取/注入失败：日志告警 + 上抛，不静默降级。
- [ ] 单测：拉取失败上抛；已是最新版本跳过注入幂等；版本比较沿用 `_template_version_id`。
- [ ] 端到端（先不含授权）：确认重启 → bot.template_config 升级到最新版本。

## P3　确认重启：授权接入（改动②-c）
- [ ] 注入 MCPSyncService（或 factory）到 `restart_bot` 可达处（沿用现有 DI 模式）。
- [ ] 注入成功后调 `refresh_mcp_scope` 授权（MCP+CLI）。
- [ ] 代码注释固化：`refresh_mcp_scope` 必须在 `update_template` 成功之后调
      （保证 `get_default_cli_items` 读到新配置）。
- [ ] 授权失败：日志告警 + 上抛，不静默。
- [ ] 端到端：确认重启后新增 mcp+cli 已授权；普通重启（false）不升级不授权、现状未被破坏。

## P4　边界与收口
- [ ] 仅 `TEMPLATE_CONFIG_CONSUMING_ENGINES` 触发拉取/注入/授权；非该集合退化为普通重启。
- [ ] openapi admission（`admission.py`）按需；错误码与文案（拉取/注入/授权失败可重试）。
- [ ] 评审守住"后端不算 diff、不存状态、无拒绝语义"。
- [ ] 移除（或不引入）旧版残留：`template_update` ext 状态、`confirm` 端点、MCP 排除表写入、
      CLI 拒绝语义——本版均不需要。

## 待办（前端，OCB 不参与，仅记录口径）
- [ ] 弹窗条件：diff 有差异 且 首次进入页面，二者同时满足才弹；否则不弹。
- [ ] 「首次进入页面」判定（按 bot_id + template_version 维护已弹标记，前端实现）。
- [ ] Path A/B 确认动作统一为 `restart{confirmed_template_update:true}`。
