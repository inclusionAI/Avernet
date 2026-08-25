---
agent: tc-code-reviewer
status: completed
created: 2026-08-19T00:50:00+08:00
iteration: 2
---

# Task 2 Gateway 最终评审报告

## 结论

**结论: PASS**

修复轮次 1/2 后的当前 diff 已经核对完成，无剩余可执行问题。本轮为只读 scoped re-review，未运行广泛测试或修改代码、配置、测试。

## 评审范围

- Avernet worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- Avernet starting HEAD: `87185a97b989d67133a997cd69ce8435fab0f47a`
- OCB worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/ocb_worktrees/openapi-bot-public-catalog/ocb-public`
- OCB starting HEAD: `eba0ccfedd54767301ebe72a7f92423c2d06e9c1`
- 两仓 HEAD 均仍等于各自 starting HEAD，审查对象为未提交的 current diff。

## 专项核对

### Catalog 的命名 401 文档

两份 served `bots.openapi.json` 均在且仅在以下两个 `GET` operation 的 `401` response 中发布了命名 examples：

- `/openapi/v1/bots/public/search`
- `/openapi/v1/bots/public/discover`

每个 response 的 example 名称、含义和 code 都准确为：

| example 名 | code | 含义 |
| --- | ---: | --- |
| `missing_or_invalid_credentials` | `401000` | 缺失或无效凭证 |
| `verified_app_only_caller` | `401001` | 已验证的纯 App caller |

全文 schema 遍历结果中没有第三个 `401001` example；它没有扩散到任何 legacy/non-catalog path。Backend 也只在 `APP_ONLY_SUBCODE_REFUSED` 的同一对 `GET` route 上抛出 `AppOnlyCallerError`，与两份 gateway 中 `user: required` / `app: optional` 的 exact rule 集合逐字匹配。

### 403 runtime / 文档一致性

- Runtime 将 `UserIdMismatchError` 映射为 `403001`。
- 共享 `USER_SCOPED_403` 文档示例发布 `403001`。
- 两个 catalog path 在两份 served schema 中的 `403` example 都为 `403001`。
- 动态 OpenAPI 测试已锁定共享 user-id mismatch example 与 runtime subcode 一致；`004-gateway-report.md` 记录的两仓目标 resolver 测试分别为 `43/43` 和 `32/32` 通过。

### Served schema 完整性与隔离

- 两个 catalog path 均只发布 `GET`；`user_id` 均为 query、`required: true`、`minLength: 1`。
- search/discover 的 success response 分别引用 `Envelope_Page_PublicBot__` / `Envelope_Page_DiscoveredPublicBot__`。
- 从两个 path 递归追踪的 closure 均为 9 个可解析组件（包括已存在的 `ErrorEnvelope`）；Avernet 与 OCB 的 catalog paths 和 closure 组件语义完全一致。
- `PublicBot` 和 `DiscoveredPublicBot` 仍为 allowlist projection；可达 closure 中未出现 primary key、binding/device id、`ext`、credential/token、environment、instance selector、internal owner/lifecycle fields、`worker_id`、`profile_key` 或 raw `recommend_response`。

### Scope 与兼容性

- OCB 对 starting HEAD 的语义 diff 严格为新增 2 个 catalog paths 与 8 个 closure components；无删除、无既有 paths/components 改动。其他 OCB schema content 保持不变。
- Avernet 的较大文本 diff 经 JSON 语义拆解后，既有 component 、top-level metadata 和其他 component subsection 均无变更；68 个既有 path 的 98 个叶子变更全部且仅为本任务明确要求的共享 403 示例 `403000 → 403001`。此处不存在生成器带入的无关 reconciliation。
- 两份 `application.yaml` 只新增两条 exact catalog 规则；generic `/openapi/v1/bots/**` 与 upstream domain routing 未改。

## Findings（按严重级别）

无 P0/P1/P2 可执行问题。

## 验证边界

- 本轮只读检查了 current diff、backend 映射/路由定义、两份 YAML、两份 schema 和 `004-gateway-report.md`。
- 两仓 targeted `git diff --check` 均通过。
- ACI 证据仍为 **PENDING**：当前改动未提交为可审核 head，也没有远程 job 提供 `casePassRate`、`lineCoverage` 和 `changeLineCoverage` 的分子/分母。形成提交后必须以正确的 base..head 运行 ACI（阈值分别为 100%、70%、90%）。
