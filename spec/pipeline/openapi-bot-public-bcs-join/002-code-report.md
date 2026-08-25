---
agent: tc-code
status: completed
created: 2026-08-20T21:35:33+08:00
iteration: 4
---

# Bot Catalog BCS metadata port — coding report

## 2026-08-21：BCS 当前页 Search 接入

- 已按 [005-bcs-search-integration-spec.md](005-bcs-search-integration-spec.md) 将 Catalog Search 的
  unavailable metadata binding 改为 BCS `/v2/bots/search` 适配器；请求仅传 `q`、`offset`、`limit` 和
  固定 `tc_bot=true`，不透传 caller 或认证头。
- BCS `bot_uuid` 解析为精确 `(bot_id, entity_id)` 后，Backend 复用 tenant-scoped ORM 二元组查询做
  当前页 inner join，并恢复 BCS 顺序。`tc_bot=true` 令正常数据下当前 BCS 页与 join 数量对齐；`total`
  始终是当前页实际 join 数量，不再聚合或二次分页。
- 非 Bot、重复、非法 UUID、上游错误和非法 JSON 均 fail closed 为既有 `502000`；响应继续由 OpenAPI
  allowlist 投影，BCS 原始字段不外泄。
- 未修改 Discover、`bot_discover_service.py`、legacy Search、BCSFuse 或 `.superpowers/`。

## 本次最终验证

| Check | Result |
|---|---|
| Catalog BCS adapter + service pytest | `108 passed`, `0 failed` |
| Catalog router/endpoint pytest (`-k catalog`) | `12 passed`, `0 failed` |
| Community architecture pytest | `169 passed`, `0 failed` |
| Ruff（本次变更的 adapter 测试）与 `git diff --check` | PASS |
| 独立代码复审 | PASS；远端 ACI 仍须在提交并推送当前 head 后执行 |

> The following sections record the earlier iteration-4 unavailable-port audit against
> `origin/dev_refactory_collaboration`; the BCS integration above supersedes that temporary
> implementation. The current adapter uses the existing BCN-qualified `HttpClient` and only the
> constant relative path `/v2/bots/search`; it adds no URL, credential, or transport configuration.

## Worktree

- Path: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- Branch: `feat/openapi-bot-public-catalog`
- Audit base: `origin/dev_refactory_collaboration@efa0b7da330d8f928b364de2b84a9012f9bbcedc`
- Audited HEAD: `8de5e1d25bc5c593bde1e2c301edf15c623169ee`
- No commit or push was performed. Existing `.superpowers/` files were not modified, staged, or removed.

## Retained complete behavior

- `/openapi/v1/bots/catalog/search` depends on a typed BCS metadata port and passes only the exact
  ordered/de-duplicated `(bot_id, entity_id)` addresses, the verified `tenant_id` / `user_id` /
  `app_id` caller projection, and request ID.
- The current production, local, and test binding is intentionally unavailable. It logs only
  request ID, candidate count, and a low-sensitivity failure category, then fails closed to the
  fixed `502000 / Catalog service unavailable` `ErrorEnvelope`.
- A future configured port must validate `kind == "bot"`, reject duplicate/unknown/blank addresses,
  inner join by the exact composite address, preserve Backend order, and paginate after the join.
- Backend remains authoritative for public fields. The OpenAPI router retains its explicit
  allowlist projection, so metadata, bindings, device data, `ext`, credentials, tokens, and
  environment data cannot enter the public response.
- Legacy `/api/v1/bot-public/search` remains Backend-only; Discover retains its BCSFuse behavior.
  The repository's original paginated query chain and behavior are retained inside the normal
  branch; the new stable `gmt_create DESC, id DESC` branch is used only when pagination is omitted
  to build the complete Catalog candidate set.

## Minimal-diff cleanup performed

| File | Pure formatting / adjacent cleanup reverted | Necessary behavior retained |
|---|---|---|
| `src/backend/src/agentclaw/community/di/modules/bot_public_module.py` | Restored the base blank line after `from __future__`; removed a format-only deletion. | Protocol-to-unavailable binding and constructor injection. |
| `src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py` | Restored the legacy search's original in-place redaction block and removed the shared sanitizer extraction that refactored adjacent code. | Catalog-only redaction, exact-address validation, fail-closed translation, and low-sensitivity diagnostics. |
| `src/backend/src/agentclaw/community/core/repository/implementations/bot/bot.py` | Kept the original paginated query layout/order in the `else` branch instead of rewriting it for every caller. | The additive unpaginated stable-order branch required for join-before-pagination. |
| `src/backend/tests/community/core/bot_public/services/test_sync_bot_config_uses_resolver.py` | Restored the base's pre-existing unused `pytest` import instead of retaining an unrelated cleanup. | Only the required metadata-port constructor argument remains in the diff. |
| `src/backend/tests/community/core/bot_public/test_bot_public_service.py` | Restored the existing `_make_bot` signature and fixed entity fixture; avoided widening an established helper solely for new tests. | A new Catalog-specific helper and behavior tests cover exact entity identity. |
| `src/backend/docs/openapi-v1/bot-public-catalog-api.zh-CN.md` | Restored the original `search` parameter wording; removed unrelated copy expansion. | Port/fixed-502/join-before-pagination documentation. |
| `src/backend/docs/openapi-v1/README.zh-CN.md` | Preserved the base sentence without reflow and added the new contract as a separate continuation. | Current fixed-502 metadata-port behavior. |

The final zero-context deletion audit (`git diff -U0 ... | rg '^-(?!-)'`) contains only required
contract/call/schema replacements and the existing pagination block being wrapped by the new
unpaginated branch. No unrelated import removal, rename, comment polish, or neighboring cleanup
remains.

## Production and generated-schema audit

- Core port DTOs/protocol/errors, unavailable implementation, service orchestration, router caller
  projection/error mapping, API protocol declaration, DI binding, Context Boundary metadata,
  repository full-candidate seam, public docs, tests, and generated schema all trace directly to
  iteration 4.
- Database search remains on the existing SQLAlchemy ORM path. No raw SQL, SQL string concatenation,
  new HTTP client, URL, credential, retry, or external configuration was introduced.
- `src/gateway/configs/schemas/bots.openapi.json` has only the required Catalog Search 502 changes:
  the example message and response description now use `Catalog service unavailable`.
- A fresh `DEPLOY_PROFILE=community` OpenAPI dump is byte-identical to the checked-in schema. Both
  files have SHA-256 `927347f1a73b27011dadd36c4b1bf92dcee8f849b5790e7b897e8205f423f4ad`.

## Iteration-4 review coverage repair

- Added three behavior tests only in `test_bot_public_service.py`: unexpected metadata exceptions
  fail closed with the low-sensitivity `invalid_metadata` log; malformed/string `ext`, `device_id`,
  passport token, and `iam_token` are redacted on Catalog results; blank Backend `bot_id` or
  `entity_id` values are excluded from metadata addresses and results.
- No production code, shared helper, API contract, formatting, or unrelated test was changed for
  this repair.
- `report_check.py` was run against a temporary tracked-worktree tree (no commit/stash/worktree
  mutation): change-line coverage is `100.00% (103/103)`, above the required `>= 90.00%`.

## Verification evidence

| Check | Result |
|---|---|
| Focused Core/router/metadata/repository/sync pytest | `157 passed`, `0 failed` |
| `report_check.py` change-line coverage | `100.00% (103/103)`; required `>= 90.00%` |
| Declarative endpoint runner (`-k catalog`) | `4 passed`, `834 deselected`, `0 failed` |
| Architecture/API-to-Core/DI resolution suite | `61 passed`, `0 failed` |
| Gateway served-schema consumer test | `11 passed`, `0 failed` |
| OpenAPI regenerate + `cmp` + JSON parse | PASS; byte-identical SHA-256 above |
| Ruff on every changed Python file except the one inherited baseline finding | PASS |
| Ruff unused import/variable check (`F401,F841`) on the same delta-clean set | PASS |
| Python style checks required by `AGENTS.md` (`E203,E211,E265`) | PASS |
| Full changed-file Ruff | One inherited `F401` at `test_sync_bot_config_uses_resolver.py:22`; the base file produces the identical finding, and the final diff does not change that import line |
| `git diff --check` | PASS |

The intentionally restored base `pytest` import is documented rather than deleted because iteration
4 requires removing unrelated cleanup from the feature diff. There are no newly introduced unused
imports or variables.
