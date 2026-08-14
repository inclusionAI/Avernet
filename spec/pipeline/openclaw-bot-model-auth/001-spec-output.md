# OpenClaw dynamic Bot model authentication repair

## Problem

In manual singlebox mode the BCS-connected OpenClaw gateways can establish their
BCS sessions but fail before producing a reply because their model provider has
no usable API credential at runtime. The affected logs report `No API key found
for provider` followed by `Embedded agent failed before reply`.

## Scope and approach

1. Treat a manual-model API-key placeholder as invalid unless it resolves to a
   real configured credential before a Bot is started.
2. Pass only the already-resolved model credential to the detached OpenClaw
   gateway process. Do not write the credential to diagnostics or change BCS,
   BaaS, provider registration, or group routing.
3. Add a low-sensitivity diagnostic that records only the credential source and
   whether a usable credential was supplied.

## Success criteria

- Manual-mode validation refuses a placeholder-only key before startup.
- Dynamic Bot startup receives a non-empty model credential when manual mode is
  configured.
- A regression test covers both the rejection and the detached-process
  environment contract without printing a secret.
- Focused shell tests and syntax checks pass; a restarted local Bot no longer
  emits the missing-provider-key error for a new model request.

## Test plan

1. Add a failing shell regression for placeholder resolution and gateway env
   propagation.
2. Implement the smallest shell change to satisfy it.
3. Run focused model-config and hybrid tests, shell syntax checks, and a live
   `merchant_hybrid` restart/status check.
