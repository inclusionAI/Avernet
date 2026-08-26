# Permissive Ask-User Answers Design

> **Boundary clarification (2026-08-26):** BaaS continues to preserve empty
> and blank Provider input for compatibility. The public Frontend-to-BCS
> contract now uses only `values: []` for a skipped question and rejects empty
> or whitespace-only string elements. See
> `src/bcs/docs/plans/2026-08-26-ask-user-empty-array-skip-design.md`.

## Context

The BaaS BCN downlink currently rejects ask-user answers whose `values` array
is empty or contains an empty or whitespace-only string. The BaaS Provider
boundary must preserve these shapes through to Engine for compatibility; BCS
uses the narrower Frontend skip contract described above.

## Goals

- Accept custom strings without comparing them to requested options.
- Accept `values: []`, `values: [""]`, and whitespace-only strings.
- Preserve the incoming value list exactly in the durable resolution and
  Engine `selectedOptions` projection.
- Keep question ID, question text, header, action, and value element types
  validated.
- Keep validation failures sanitized so answer content is not logged or
  returned.

## Non-goals

- Allowing missing `values`.
- Allowing non-string elements in `values`.
- Allowing submit with no answer entries.
- Looking up requested options or rewriting custom values to `other`.
- Changing Engine source or wire methods.

## Contract and normalization

For every submitted answer, `values` is a required `list[str]` with no minimum
length and no non-blank constraint. An empty list or blank string represents a
skip. BaaS joins values exactly as before for the summary, `values`, and
`answers` projections; therefore a skip produces an empty or whitespace value
in those projections. `selectedOptions` preserves the original nested arrays,
including empty arrays and blank strings.

The outer `answers` object must remain non-empty on submit. BCS remains
responsible for ensuring all requested question IDs are represented before the
Provider call.

## Compatibility and propagation

This is a backward-compatible input relaxation. Existing non-empty answers
produce byte-for-byte equivalent Engine fields. BCS must make the corresponding
relaxation on its dev-based branch; this BaaS change is based on REL20260821.

## Verification

- Transport model tests accept all skip representations and continue rejecting
  non-string values and missing required fields.
- Service tests assert exact summary, value maps, answer maps, and nested
  `selectedOptions` output for skip values.
- The finite sanitized validation-ack test uses a genuinely invalid non-string
  value instead of a blank value.
