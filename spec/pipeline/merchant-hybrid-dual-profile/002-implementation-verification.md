# Merchant Hybrid Dual-Profile — Implementation and Verification

## Delivered interface

```bash
SINGLEBOX_MODEL_CONFIG_MODE=home \
./scripts/singlebox.sh start merchant_hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```

`merchant_hybrid` accepts the three arguments only as a single lifecycle
target. `--exclusive-profile-dir` is matched against the OpenClaw manifest's
`bots[].source`, so the legacy port calculation stays at 30601, 30611, and
30631 after `platform-data` is omitted. The legacy `start bots --profile-dir
...` path remains unfiltered and starts all four original OpenClaw personas.

The new Claude profile contains one `normalCC` platform-data bot on relay
port 18913. It uses an isolated configuration directory and workspace, and
has no credential or model-provider setting in the repository. With
`SINGLEBOX_MODEL_CONFIG_MODE=home`, an absent isolated config directory is
created without copying a credential; the relay receives the existing home
model settings only as its local settings source.

## Runtime behavior

- The lifecycle is relay → BaaS → Backend → BCS → three OpenClaw bots → one
  normalCC bot → Provider bridge → Frontend, with exact reverse stop and
  command-owned rollback.
- A profile run selects its own bot/provider state and credential files, so it
  cannot reuse a previous three-role mixed topology registration.
- The Provider BCS card is deliberately named `平台数据分析（当前）`. The suffix
  differentiates the current, credential-backed card from retained historical
  Provider cards; direct textual mention tests must use that displayed name.
- BaaS now enables the existing `mixed-claude-code` overlay when either the
  legacy JSON configuration or the single Claude profile is active. This is
  required for real normalCC adapter routing and Backend binding lookup;
  without it, BaaS starts but `chat.send` fails at local-stub binding lookup.
- `CLAUDE.md` expansion is bounded to the `platform-data` profile directory.
  It rejects missing, absolute, escaping, cyclic, and over-five-level imports.
  Startup logs only count, character count, and SHA-256—not role text.

## Verification evidence

The following focused checks passed:

```text
bash -n (singlebox and affected modules/tests)
bash scripts/test_merchant_hybrid.sh
bash scripts/test_singlebox_service_guards.sh
bash scripts/test_singlebox_mixed_claude_bots.sh
bash scripts/test_claude_relays.sh
src/backend/.venv/bin/pytest -q src/backend/tests/community/core/bot_management/test_engine_provisioning_strategy.py
gateway: egg-bin test test/system-prompt.test.ts test/server.test.ts
gateway: npm run prepublishOnly
node --check scripts/test_merchant_hybrid_live.mjs
git diff --check
```

Focused results: merchant profile shell suite passed; service guards passed;
legacy mixed shell suite passed; three-relay health suite passed; Backend
strategy suite passed 27 tests; gateway prompt/server suite passed 39 tests;
and the gateway package build completed.

Live acceptance used an isolated BCS group with the exact topology (three
OpenClaw plus one current Claude Provider bot). Group creation caused exactly
one Provider `chat.inject` and four successful SessionContext recipients. A
read-only targeted `chat.send` to the current platform-data card returned a
Claude final through BCS Provider → BaaS real adapter → relay. The final
contained `结论`, `关键结果`, `校验`, `缺口`, and `交接`; only boolean/count
metadata was retained by the acceptance probe.

## Reviewer checklist

1. Read `scripts/modules/merchant_hybrid.sh` for strict input validation,
   static ports, lifecycle, ownership checks, and rollback.
2. Compare `scripts/4bots_merchant_operations_profile/bots.json` with
   `scripts/4bots_merchant_operations_profile_for_claude/bots.json` to verify
   one-for-one `platform-data` replacement.
3. Review `src/engine/src/engine/community/claude_code_gateway/src/system-prompt.ts`
   and its tests for import containment and non-content diagnostics.
4. Review `scripts/modules/baas.sh` and the matching service-guard regression
   for the real BaaS binding overlay. This is the fix that turns apparent
   startup success into working normalCC `chat.send`.
5. Re-run the command above, then run `node scripts/test_merchant_hybrid_live.mjs`
   from the worktree. The probe prints no group ID, reply text, or credential.
