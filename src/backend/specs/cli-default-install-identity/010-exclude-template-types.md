---
agent: tc-spec
status: complete
created: 2026-09-14T00:00:00+08:00
supersedes: ["003-review-report.md:R-01 (partial exception)"]
amends: ["001-spec-output.md"]
---

# Default CLI Manifest: `exclude_template_types` Match (Revision 010)

This revision amends the certified contract in `001-spec-output.md` (R-01 in
`003-review-report.md`) to introduce an optional, **config-driven** deny-list
match field for the otherwise-unserved `claude_code` templates. It is filed to
satisfy arch.rules.md Rule 1 (contract changes update specs/docs in the same
change) and Rule 16 (propagation analysis).

## 背景与动机

`001-spec-output.md` 将 Default CLI manifest 的 profile 匹配设计为**精确逻辑
引擎与模板匹配**（R-01 certified PASS），一期仅 `openclaw` 与
`claude_code/generalCC` 命中，其余（含 `claude_code/normalCC`、`claude_code`
任意非 `generalCC` 模板、`aicoding`）一律 fail closed。

`template_type` 是**运营可扩展、后端不可穷举**的维度（会随时新增，例如
`mcptestpq`）。仅靠精确 `template_type` 枚举无法覆盖未来模板，导致
`claude_code` 非 `generalCC`/非空模板的 bot 在 `supports_profile()` 被拒，触发
`CallerIdentityReadOnlyError`，阻挡其 CLI caller/owner (IAM) 变更。本修订在不改
变已认证精确匹配语义的前提下，引入可选匹配字段 `exclude_template_types`，
为上述不可穷举模板提供一条**配置驱动**的命中路径。

## 与既有不变量的关系（关键）

`001-spec-output.md` §132、§298 明确禁止"沿用 Claude Code 到 aicoding 的默认能
力分桶"。本修订**延续**该分离不变量：profile 命中仍由部署 manifest 驱动，匹配
器保持 engine-agnostic 的纯字符串/列表匹配，**不调用**
`claude_code_uses_aicoding_runtime()` / `default_capabilities_engine_bucket`
等 runtime-identity 谓词。

> 因此，复用 `runtime_identity.py` 中的判定函数不是本修订采纳的机制——那会在
> manifest 层引入第二条、引擎私有的判定副本，并破坏 001 的分离不变量。`normalCC`
> 的排除清单经此显式声明其一致来源为运营约定，但 CLI manifest gate 与
> runtime-identity 谓词解耦保留，以便各自演进；若后续 runtime-identity 侧（如
> `engine_form` marker）需要反映到 CLI manifest gate，应以新修订单独评估，而非在
> manifest 内硬编码 runtime 调用。

（这一条对应评审 Finding 2。评审员二已将"复用 canonical 谓词"判为非 blocker 的
follow-up；本 PR 依 001 §132/§298 不采纳，理由如上。）

## 合同语义

### 新增可选字段 `profiles[].match.exclude_template_types`

- 类型：非空 string 数组（如 `[normalCC]`），可选；与 `template_type` **互斥**
  ——同一 `match` 不得同时含二者（加载期校验失败）。
- 命中条件：`match.engine_type` 等于 `active_engine` **且** `template_type` 非空
  **且** `template_type` 不在列表中。
- 每个 entry 必须为非空字符串，加载期校验（`_parse_profiles`）。

### Profile 顺序为合同不变量

`required_cli_items` 按 manifest `profiles` 声明顺序"首个命中即返回"。对
`claude_code`：`claude-code-generalcc-default`（`template_type: generalCC` 精确）
**必须声明在**任何 `exclude_template_types` 兜底之前，否则 `generalCC` 会被兜底
提前命中。resolver 已加注释固化该顺序契约；测试
`test_supported_profiles_resolve_exactly_to_two_default_clis` 断言
`generalCC`/`mcptestpq`/`normalCC`/None 的命中分布。

## 命中矩阵

| active_engine | template_type | 命中 profile | supports_profile |
|---|---|---|---|
| openclaw | 任意/None | openclaw-default | ✅（不变）|
| claude_code | generalCC | claude-code-generalcc-default | ✅（不变）|
| claude_code | mcptestpq 等非空≠normalCC | claude-code-custom-default | ✅（本期新增）|
| claude_code | normalCC | 无 | ❌ fail closed（不变）|
| claude_code | None/空 | 无 | ❌ fail closed（不变）|
| aicoding | * | 无 | ❌ fail closed（不变）|

## 对 R-01 的精确 supersede

R-01"仅 `openclaw`、`claude_code/generalCC` 命中；其余 fail closed"对
`openclaw` 与 `claude_code/generalCC` 精确路径**继续成立**；**唯一例外**是新增
`claude-code-custom-default` 兜底 profile 允许 `claude_code` 非 `generalCC`/非空/
非 `normalCC` 模板命中。该例外经本文档显式声明并测试约束，不放宽到 `normalCC`、
空或 `aicoding`/`moltis`/`hermes`。

## 传播分析（arch.rules.md Rule 16）

- **受影响消费者**：仅 `CliPassportScopeReconciler` 两处使用 manifest 解析。
  - `supports_profile()` —— CLI call-type 变更的 phase-one gate。
  - `reconcile()` / `_build_snapshot()` —— 设备 Bootstrap 与编辑后收敛，据此向
    Passport scope 注入 `default_cli_codes`。
  无其他消费者读取该 manifest。
- **兼容性**：
  - 既有 profile（`openclaw-default`、`claude-code-generalcc-default`）无
    `exclude_template_types` 字段 → 匹配分支不触发，所有先前命中输入（openclaw/*、
    `claude_code/generalCC`）逐字节不变。
  - 先前被拒的 `claude_code` 非 `generalCC`/非空模板现在命中（本修订目标）；
    `normalCC`、空、`aicoding` 仍 fail closed。
  - 字段为纯新增，未删除既有字段，无破坏性、无需迁移。
- **安全影响**：`supports_profile()` 是 `update_cli_call_type` 授予 CLI
  caller/owner IAM 变更权能的门禁。本放宽有界——仅非空且非 `normalCC` 的
  `claude_code` 模板通过；`normalCC`、空保持 fail closed。与创建期
  default-capability 路由（已将这类模板视作 aicoding-runtime bot 并授予 aicoding
  CLI 默认项）一致，即修复一处既存不一致，而非新增任意授权。
- **下游副作用**：对新增命中的 bot，Bootstrap/reconcile 会向其 Passport scope 注入
  `dataphin`、`deepinsight-cli`；这是 manifest "Default CLI profile" 的设计用途，
  且与该 bot 已被归类为 aicoding-runtime 一致。
- **迁移/废弃**：无字段移除；`exclude_template_types` 为可选新增字段。无需数据迁移。

## 代码与测试证据

- `cli_capabilities.py`：`required_cli_items` 新增 `exclude_template_types` 分支
  （仅当字段存在触发）+ `_parse_profiles` 校验（类型 + 与 `template_type` 互斥）+
  顺序契约注释。
- `configs/cli-capabilities.yaml`：新增 `claude-code-custom-default` 兜底 profile。
- 测试：
  - `test_supported_profiles_resolve_exactly_to_two_default_clis`：断言
    generalCC(✅)/mcptestpq(✅)/normalCC(❌)/None(❌)/空(❌)/aicoding(❌)。
  - `test_reconciler_exposes_phase_one_cli_profile_gate`：`supports_profile`
    闸门断言 mcptestpq→True、None→False、normalCC→False。
  - `test_manifest_rejects_invalid_catalog_and_profile_schema`：新增
    `bad_exclude_not_list`/`bad_exclude_empty_entry`/`exclude_with_template_type`
    fail-closed 校验。
  - `caller_identity` + `mcp/services` + `devices` + `engine_runtime` 合计
    **1184 passed**，零回归。
