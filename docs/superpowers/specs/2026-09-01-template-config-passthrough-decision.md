# OpenAPI template_config 查询面全字段透传决策

- **日期**：2026-09-01
- **决策**：产品拍板（平台负责人）：公开查询面（`/openapi/v1/bots`、`/openapi/v1/bots/all`、`/openapi/v1/bots/{bot_id}`）对 `template_config` 由白名单投影改为**原样透传**——存储快照（`ac_templates.ext`）深拷贝直接返回，不做任何字段过滤。
- **范围**：所有模板 bot（aicoding/applicationCoding、normalCC 模板工厂、personalCoding 等），三类一致透传。
- **背景**：集成方反馈「老 API 创建的 aicoding，经 openapi all/bots 返回时 template_config 字段缺失」。根因即白名单投影：老面（`/api/bots`）原样回传 `ac_templates.ext`，新面只保留 6 个 key（`code_repos`/`devflow_workflow`/`engine_form`/`template_key`/`template_uid`/`yuque_kb_repos`），其余（`devflow_workflows` 复数、`model`/`runtime`、`token`、`bot_template_config` 等）全部被丢弃。

## 1. 安全取舍（已确认接受）

原白名单的存在理由：`template_config.token`（可为明文业务 token）与
`bot_template_config.ext_config.thetaKey`（`enc:v1:` 密文）属于密钥材料，列表响应不应回显。

本决策的反驳理由（已由平台负责人确认接受）：

1. 三个查询面均为 **owner-scoped**（`user_id` 必须是被验证的 owner 本人，或经 owner 显式授权的应用调用方）——回显的是调用方自己创建时传入的输入，而非泄露他人秘密。
2. 密文回显的离线攻击前提（响应落日志/缓存）由网关与客户端侧承担；平台侧不再替调用方做此判断。
3. 消除两面（老 `/api` vs 新 `/openapi`）行为分叉，`devflow_workflows`（复数，引擎读路径的权威键）等字段不再静默丢失。

**若未来要恢复过滤，属产品决策 revert，不是 bug fix**；恢复时须先阅本文件与
`template_public_view.py` 模块 docstring。

## 2. 实现锚点

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `core/bot_management/template_public_view.py` | 删除 `_PUBLIC_TEMPLATE_KEYS`；`project_template_config_for_public` 改名 `template_config_for_public`，实现为「非 Mapping → None；否则 deepcopy 原样返回」 |
| 2 | `adapters/http/openapi_v1/bots/router.py`（`_to_bot`） | 调用改为 `template_config_for_public`；`template_type` 空时仍返回 null（"null without a template" 契约不变） |
| 3 | `core/bot_inventory/services/bot_inventory_service.py`（`_attach_page_templates`） | 同上改调用；attach 语义 verbatim |
| 4 | `adapters/http/openapi_v1/bots/schemas.py` | `Bot`/`BotInventoryItem` 的 template_config description 改为 verbatim + 敏感提示 |

不变的部分：

- 「无 template → null」的 `template_type` 门控不变。
- 深拷贝契约不变（调用方可安全变更返回值，不别名到存储快照）。
- 创建路径的 PUBLIC 校验（server-managed 字段拒绝清单，包括用户传 `engine_form` → 422）不变——透传只放开**读**面。

## 3. 对既有文档的修订

- `2026-08-31-engine-vocabulary-template-form-design.md` §6 第 7 条验收
  「`token`/`bot_template_config`（thetaKey）绝不出现（扫描断言）」——**被本决策撤销**，断言已改为「原样回显」。
- `plans/2026-08-28-bot-template-space-fields.md` 记录的是当期实现（白名单），保留为历史，不回改。

## 4. 钉死测试（已落地）

- `tests/community/core/bot_management/test_template_public_view.py`：非 Mapping → None；`{}` 原样；全字段（含 `token`/`thetaKey`/`devflow_workflows`/`model`/`runtime`）等值透传；深拷贝不别名。
- `test_bots_endpoints.py::test_list_bots_carries_template_snapshot_and_space`：/bots 列表全量等值。
- `inventory/test_inventory_handlers.py::test_list_inventory_carries_template_snapshot_verbatim`：/all 全量等值。
- `test_bot_inventory_service.py::test_page_slice_attaches_template_config_verbatim`：分页 attach 全量等值。
