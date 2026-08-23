---
agent: tc-pr
status: pending
created: 2026-08-23T00:00:00+08:00
---

# Catalog Search BCS 元信息透传 PR 报告

## 范围

- Repository: `inclusionAI/Avernet`
- Base: `origin/dev_refactory_collaboration@53e4c961af5e28644e5b40e08f6886132477b53c`
- Topic branch: `rebase/openapi-bot-catalog-bcs-metadata-on-dev_refactory_collaboration`
- PR: 创建后补充链接和最终 head SHA。

## 改动摘要

- Catalog Search 改为调用固定 BCS 路径 `/bots/search`。
- 对精确 `(bot_id, entity_id)` join 成功的记录，按 allowlist 透传七个可选 BCS 字段：`visibility`、`is_online`、`actor_kind`、`is_friend`、`friend_ext`、`friend_check_in_strategy`、`user_visibility`。
- 字段缺失或值为 `null` 时省略；未点名的 BCS 原始字段继续不对外暴露。

## 本地验证

| 检查 | 结果 | 证据 |
|---|---|---|
| Catalog Search 定向测试 | PASS | `163 passed` |
| 架构门禁 | PASS | `15 passed` |
| Ruff | PASS | 定向文件检查通过 |
| OpenAPI JSON 与字段存在性 | PASS | JSON 可解析，两个公开模型均包含七个字段 |
| `git diff --check` | PASS | 相对 base 无空白错误 |
| Pyflakes | NOT RUN | 当前 Backend 虚拟环境未安装该工具；Ruff 已覆盖未使用 import/名称检查 |

## 远端检查

PR 创建后以 GitHub 实际检查状态为准；本地通过不代表远端 ACI 通过。
