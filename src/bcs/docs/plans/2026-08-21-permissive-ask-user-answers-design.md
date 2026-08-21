# Permissive Ask-User Answers Design

## Context

BCS currently treats `allowOther` as a server-side authorization flag. When it
is absent or false, BCS rejects an ask-user value that is not one of the
Provider's original options. BCS also rejects empty and blank values. This
conflicts with the Workbench interaction model: `allowOther` is a presentation
hint, users may enter custom answers, and a skipped question is represented by
an existing answer entry whose `values` array is empty or contains blank text.

## Goals

- Accept custom string values regardless of `allowOther` and option contents.
- Treat an absent `allowOther` as allowing custom input.
- Treat a non-boolean `allowOther` as absent, remove it from the canonical
  requested payload, and emit a warning without question or answer content.
- Accept `values: []`, `values: [""]`, and whitespace-only string values as
  explicit skipped answers.
- Preserve answer values exactly when forwarding them to the Provider.
- Keep all other ask-user structural checks.

## Non-goals

- Allowing a submit to omit a question ID entirely.
- Allowing non-string elements in `values`.
- Changing exec or mode-switch decision validation.
- Giving `allowOther` server-side authorization semantics.

## Contract

`answers` must still contain every requested `questionId` exactly once. Each
answer must contain a `values` array, but the array may be empty and its string
elements may be empty or whitespace-only. A non-multi-select question accepts
zero or one value; a multi-select question accepts zero or more values.

For questions with options, BCS does not compare submitted values with
`options[].value`. `allowOther` remains available to clients as a UI hint only.
Both missing and malformed `allowOther` values use the permissive default. An
explicit false value may hide custom input in a conforming UI, but it does not
cause the server to reject a custom value submitted by another compatible
client.

## Data flow

Before BCS validates and stores an ask-user request, it removes every
non-boolean `questions[].allowOther`. The sanitized payload is authoritative
for duplicate detection, replay, Frontend publication, and later resolution.
The warning identifies only the run, interaction, and question index.

During resolution, BCS validates the answer map and value types, augments each
answer with the authoritative stored question/header, and forwards all values
unchanged to the Provider.

## Compatibility and propagation

This is a backward-compatible relaxation for Frontend clients and Providers.
The BaaS Provider implementation must independently relax its transport and
normalization checks so skipped values accepted by BCS are not rejected at the
next boundary. Existing valid non-empty option answers are unchanged.

## Verification

- BCS service tests cover missing, false, and malformed `allowOther`, custom
  values, empty arrays, empty strings, whitespace, and unchanged forwarding.
- Existing authorization, idempotency, option-shape, and decision tests remain
  green.
- The Provider 2.0 protocol document records the new semantics.
