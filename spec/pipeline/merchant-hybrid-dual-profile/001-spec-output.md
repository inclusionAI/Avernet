# Merchant Hybrid Dual-Profile Singlebox

## Goal

Provide one lifecycle group that starts the existing merchant OpenClaw profile
without its `platform-data` entry and replaces it with a BaaS-managed Claude
Code Provider bot.  The command is:

```bash
SINGLEBOX_MODEL_CONFIG_MODE=home \
./scripts/singlebox.sh start merchant_hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```

The result is exactly three OpenClaw bots and one Claude Code bot.  Existing
`bots --profile-dir` behavior and the fixed 5+3 `--claude-bots-config` mixed
topology remain unchanged.

## Inputs and contracts

- `--profile-dir` remains the source of OpenClaw entries.
- `--exclusive-profile-dir` accepts one `bots[].source` value, not a path.  It
  is valid only with `merchant_hybrid` and excludes that OpenClaw entry before
  readiness, session, port, and lifecycle operations.
- `--claude-profile-dir` is valid only with `merchant_hybrid`.  It contains a
  single version-1 Claude profile manifest and a `platform-data/CLAUDE.md`
  root document.
- The Claude profile describes one `runtime.type=claude_code` record with an
  isolated relay port, workspace, `permission_mode=bypassPermissions`, and a relative
  `system_prompt_md`.  Model credentials and provider configuration remain in
  the caller's existing Claude configuration; no credential is copied into the
  profile or emitted by diagnostics.

## Persona loading

`CLAUDE.md` is the root document.  It may import only relative Markdown files
under its own profile directory by `@file.md` lines.  Relay startup resolves
the imports once, rejects missing/escaping/cyclic/deeper-than-five imports,
and exposes only the expanded text as the stable role system-prompt prefix.
The relay continues to use its configured workspace and does not create a
workspace `CLAUDE.md`.

The gateway uses that prefix for ordinary sends and continuation flows before
it appends bounded BCS conversation context.  Injected messages remain
conversation data and retain the existing cold-start replay behavior.

## Lifecycle

`merchant_hybrid` starts in this order:

```text
Claude relay -> BaaS -> Backend -> BCS -> three OpenClaw bots
-> Claude normalCC bot -> BCS Provider bridge -> Frontend
```

Stop is the reverse order.  BCS enables only loopback Provider callbacks for
this opt-in topology.  Startup validates all three profile parameters before
stopping or starting any component and rolls back only services it started.

## Acceptance

1. Existing four-bot `start bots --profile-dir ...` remains unchanged.
2. The hybrid command rejects missing or mismatched profile/exclusion inputs
   and produces exactly three OpenClaw sessions plus one current Claude
   Provider bot on success.
3. `CLAUDE.md`, `WORKFLOW.md`, `KNOWLEDGE.md`, `RULES.md`, `OUTPUT.md`, and
   `MEMORY.md` are all present in the Claude prompt; no profile text or secret
   reaches diagnostics.
4. Status, stop, and restart operate on the same hybrid state and never stop a
   listener owned outside this checkout.
5. A local smoke group can deliver a no-side-effect message to the Claude
   platform-data bot and receive a final response through BCS Provider ->
   BaaS -> relay.
