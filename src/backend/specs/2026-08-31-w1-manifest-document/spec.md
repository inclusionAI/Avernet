# W1 — manifest 文档：存储、schema v1、能力、API（#1469）

> 计划来源:`docs/superpowers/plans/2026-08-31-bot-config-manifest-implementation-plan.md` Phase A。
> 设计来源:`docs/superpowers/specs/2026-08-31-bot-config-manifest-design.md` §4.1。
> 验收标准:issue #1469 为唯一权威,本文只列执行口径。

## 交付物

- 新模块 `core/bot_config_manifest/`（Context Boundary README）
- 表 `ac_bot_config_manifest`（uk `(avernet_tenant, manifest_key)`，`manifest_key` 长度前缀 sha256）
- 仓储协议/实现对（`core/repository/{protocols,implementations}/bot/`）
- schema v1 解析+校验（`manifest_schema.py`，六类目+sources+script）
- 能力解析器（`capabilities.py`，单函数两入口，fail closed）
- 服务+协议（自有 core 模块定义，`api/` re-export，_PAIRS 注册）
- 路由 4 条 + ADMISSION 4 行 + `bots.openapi.json` 手工增量 + `BCM_API_ENABLED` 开关
- 一期范围修订（2026-08-31）：无任何 apply；`engine_config`/`cli_tools` capability=false（无物化器，PUT 拒绝）

## 验收 → 测试映射

| issue 验收 | 测试 |
| --- | --- |
| 无 manifest 读空文档非错误 | 服测 `test_get_returns_empty_document_when_absent` |
| 校验拒绝清单（多来源/未声明 from/git+digest/auth 错位/apply_once/未知占位符/路径穿越/嵌套/限额） | `test_manifest_schema.py` 规则码矩阵 |
| PUT all-or-nothing 带逐条原因 | 服务测 `test_put_invalid_rejects_without_write` |
| 能力单函数读写共用+未知引擎 fail closed | `test_capabilities.py` |
| 跨租户同 bot_id 隔离 | 仓储+API 层租户用例 |
| script 正文（引号/`$(id)`/`{token}`）往返一致 | 仓储 round-trip + API round-trip |
