# Merchant Hybrid — Partial BCS Start Guard

## Problem

The merchant-hybrid Provider bridge is intentionally a loopback callback from
BCS to the local bridge. Starting only `bcs` or `bcs_frontend` later copies the
standard BCS template and resets `security.outbound_url.allow_loopback=false`.
The persisted Provider Bot card remains visible, but every `chat.send` is
rejected before the bridge or Claude Code relay is called.

## Required behavior

1. If this checkout has a current merchant-hybrid Provider registration state,
   a BCS start without the Claude merchant profile must fail before it replaces
   the BCS runtime configuration.
2. The failure must tell the operator to use the full `merchant_hybrid`
   lifecycle, or stop that lifecycle first.
3. A full merchant-hybrid start, where the Claude profile is enabled, must
   continue to start BCS and enable loopback-only callbacks.
4. No credentials, Provider IDs, or chat content may be emitted by the guard.

## Verification

- Shell regression: a tracked merchant Provider state rejects an ordinary BCS
  start and does not call the binary launcher.
- Shell regression: the same state permits BCS start when the Claude profile
  is enabled.
- Local acceptance: restart the full merchant-hybrid command, run the isolated
  live group test, and verify that the Claude Provider emits a final response.
