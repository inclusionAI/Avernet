# Merchant Hybrid: minimal 3 OpenClaw + 1 Claude Code

## Goal

On `hybrid_base_on_dev`, add an opt-in `merchant_hybrid` singlebox group that
starts the existing merchant OpenClaw profile minus `platform-data`, plus one
Claude Code `platform-data` Provider bot. The existing four-bot profile and
all existing singlebox targets keep their behaviour.

```bash
SINGLEBOX_MODEL_CONFIG_MODE=home \
SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED=1 \
./scripts/singlebox.sh start merchant_hybrid \
  --profile-dir scripts/4bots_merchant_operations_profile \
  --exclusive-profile-dir platform-data \
  --claude-profile-dir scripts/4bots_merchant_operations_profile_for_claude
```

## Minimality boundaries

- The original OpenClaw role definitions remain intact; the manager's one-shot
  reference paths point at the already-installed workspace
  `skills/bcs-coordination/` directory.
- No BCS Rust, BCS plugin, frontend, Backend strategy, BaaS process-manager,
  BaaS uplink, or BaaS sandbox changes are allowed.
- The one relay uses BaaS's pre-existing default `ws://127.0.0.1:18900`, so no
  per-bot relay environment propagation is needed.
- BCS changes are restricted to generated runtime configuration that permits a
  loopback Provider webhook for this opt-in group.

## Runtime contract

`merchant_hybrid` validates exactly three active OpenClaw entries and exactly
one `platform-data` Claude Code profile entry. It starts relay, BaaS, Backend,
BCS, three OpenClaw bots, one normalCC Backend bot, one BCS Provider bridge,
then frontend. Stop is reverse order. Runtime credentials are 0600 files and
diagnostics log identifiers, sizes and hashes only.

The relay expands `CLAUDE.md` imports only within the Claude profile root and
passes the resulting role prefix together with bounded conversation context.
`chat.inject` remains a message injection: an inject received before the first
send is replayed into that first model turn; an inject after a native Claude
session exists is appended to its JSONL leaf and retained in relay history.

## Model policy

For this opt-in group, all four Bot defaults and the BCSFuse-backed SOP model
roles use the primary model selected by Singlebox. In `manual` mode that model
comes from the repository-local `.env.local`; in `home` mode it comes from the
imported OpenClaw configuration. The launcher validates the configured primary
but does not synthesize another model or rewrite the generated OpenClaw
default. The Claude relay receives the same model ID at runtime, while the
reusable Claude role profile remains model-agnostic. Credentials and endpoints
remain sourced from local runtime configuration and are never written to
version-controlled profile files.

## Acceptance

1. `start bots --profile-dir scripts/4bots_merchant_operations_profile` still
   starts four OpenClaw bots.
2. The hybrid command starts three OpenClaw bots, one relay at 18900, one
   normalCC adapter and one discoverable `平台数据分析（当前）` Provider bot.
3. A BCS group can receive a streaming Claude reply and a later direct Claude
   question can identify a unique marker delivered through `chat.inject`.
4. Invalid profile input, an external listener on a claimed port, and failed
   component startup fail safely without stopping outside-checkout processes.
5. The generated manager workspace can read the installed custom-collaboration
   skill and schema using the paths required by its one-shot SOP instructions.
