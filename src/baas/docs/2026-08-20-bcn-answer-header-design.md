# BCN Ask-User Answer Header Design

- Date: 2026-08-20
- Status: Approved
- Scope: BaaS Engine-to-BCN requested conversion, BCS `interaction.resolve`
  enrichment, and BaaS BCN-to-Engine normalization

## Problem

An ask-user resolve currently uses the `answers` object key (`questionId`) as
the display header when BaaS builds the Engine resolution:

```json
{
  "answers": {
    "deploy_target": {
      "question": "What's your deploy target?",
      "values": ["staging"]
    }
  }
}
```

This conflates two independent protocol concepts. `questionId` is a stable
question identity, while `header` is Provider-owned display text. BCN permits
Providers to assign different values and does not define `questionId` as a
fallback header.

## Goals

- Preserve `questionId` only as the outer answer identity.
- Give every BaaS-produced question an independent deterministic identity and
  carry `header` only as Provider-owned display text.
- Forward the trusted requested `header` in each answer sent from BCS to the
  Provider.
- Require a non-empty answer `header` at the BaaS Provider boundary.
- Use `answer.header`, rather than `questionId`, for the Engine `answer`,
  `message`, and `values` projections.
- Keep Frontend resolve submissions values-only.
- Keep Engine request formats unchanged.

## Non-goals

- Making `header` globally required for every BCN Provider.
- Falling back from a missing answer header to `questionId`, `question`, or a
  Frontend-supplied field.
- Changing exec or mode-switch resolution.
- Changing how custom ask-user values are represented.

## Contract

Frontend continues to submit only values:

```json
{
  "action": "submit",
  "answers": {
    "deploy_target": {"values": ["staging"]}
  }
}
```

BCS uses the outer `questionId` to locate the authoritative stored requested
question. It forwards the stored `question` and, when present, the stored
`header`:

```json
{
  "action": "submit",
  "answers": {
    "deploy_target": {
      "header": "部署环境",
      "question": "What's your deploy target?",
      "values": ["staging"]
    }
  }
}
```

The outer key remains `questionId`; it has no display fallback semantics.

## BCS Behavior

BCS keeps `header` optional in the global requested SSE contract. During
ask-user submit augmentation it:

1. Finds each stored requested question by `questionId`.
2. Overwrites any Frontend-supplied `question` with the stored question text.
3. Removes any Frontend-supplied `header`.
4. Inserts the stored header only when it is a non-empty string.
5. Does not synthesize a header when the stored question omits it.

The canonical augmented resolution is used for both the idempotency
fingerprint and the Provider request. Therefore untrusted presentation fields
cannot change retry identity or reach the Provider.

## BaaS Behavior

BaaS treats answer `header` as required because BaaS-produced ask-user
requested events always carry one. Its BCN request model and internal domain
answer require non-empty `header`, `question`, and `values`.

On Engine-to-BCN requested conversion, BaaS requires every Engine question to
contain a non-empty header. A missing or whitespace-only header drops the
whole unusable interaction through the existing sanitized warning path; BaaS
does not synthesize a header. BaaS assigns the independent deterministic
identity `question_<source position>` and ignores any Engine questionId for
BCN identity. Repeated headers remain separate questions with distinct IDs
and produce a content-free warning.

For answers in JSON object order, BaaS builds:

```json
{
  "decision": "submit",
  "answer": "部署环境: staging",
  "message": "部署环境: staging",
  "values": {"部署环境": "staging"},
  "answers": {"What's your deploy target?": "staging"},
  "selectedOptions": [["staging"]]
}
```

`answer` and `message` join question summaries with `；`. Values within one
answer are joined with `，`. `answers` remains keyed by the full question text,
and `selectedOptions` remains an ordered two-dimensional array.

If headers repeat, normalization does not reject or stall the interaction.
The textual summary retains every answer, while the Engine `values` object
uses stable last-write-wins behavior and BaaS emits a structured warning that
contains no question, header, or answer content.

## Validation and Errors

- BCS does not reject a globally valid Provider interaction merely because its
  requested question omitted optional `header`.
- BCS forwards no header for such a question and performs no fallback.
- BaaS rejects an ask-user submit whose answer omits `header`, supplies a
  non-string header, or supplies a whitespace-only header.
- BaaS does not publish an ask-user requested event if any Engine question
  omits a non-empty header; the rejected event does not consume an SSE
  sequence number.
- The existing finite Provider ACK maps this BaaS validation failure to
  `ok=false`, `retryable=false`; no malformed Engine request is queued.
- Cancel, exec, and mode-switch requests are unaffected.

## Compatibility and Deployment

This is an intentional tightening of the BaaS Provider contract. A new BaaS
does not accept legacy BCS ask-user answers without `header`. Deploy BCS before
BaaS so BaaS-originated interactions are enriched before strict validation is
enabled. Old BaaS deployments may ignore the additional field until upgraded.

## Tests

BCS contract and service tests cover:

- Frontend values-only input becomes Provider answers containing stored
  `question` and stored `header`.
- Frontend-supplied `question/header` cannot override stored values.
- Missing stored header is omitted without fallback.
- Provider transport receives the enriched JSON unchanged.

BaaS adapter and service tests cover:

- Engine-to-BCN conversion uses indexed question IDs independent of header.
- Missing/empty requested headers drop the interaction without consuming seq.
- Duplicate requested headers remain distinct and warnings contain no content.
- Request parsing and domain mapping preserve required header.
- Missing, empty, whitespace-only, and non-string headers are rejected.
- Engine summary and `values` use header while `answers` uses question.
- `questionId` differs from header without leaking into Engine display fields.
- Duplicate headers use last-write-wins and emit a content-free warning.
- Cancel, exec, and mode-switch regression behavior remains unchanged.
