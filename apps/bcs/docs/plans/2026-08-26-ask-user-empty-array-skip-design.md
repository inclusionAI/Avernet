# Ask-User Empty-Array Skip Design

- Date: 2026-08-26
- Status: Approved
- Scope: BCS Provider 2.0 `interaction.resolve` validation and forwarding

## Problem

Frontend submits one answer object for every requested ask-user question. When
the user intentionally skips a question, Frontend preserves that question's
identity and sends `{"values": []}`. BCS currently rejects the empty array
before the resolution reaches the Provider, even though BaaS accepts an empty
`values` list and maps it to an empty Engine `selectedOptions` row.

The rejection was introduced while adding explicit `values`/`customValues`
classification. That classification needs non-empty strings when a value is
present, but it does not require every answer array to contain a value.

## Contract

- `submit` must contain exactly one answer object for every requested
  `questionId`.
- Every answer must contain a `values` array.
- `values: []` is the only canonical representation of an intentionally
  skipped question.
- A non-empty `values` array must contain only non-empty, non-blank strings.
- Single-select and free-text questions accept zero or one value.
- Multi-select questions accept zero or more values.
- `values: [""]` and whitespace-only values are invalid. Frontend must
  normalize an empty input to `values: []`.
- Frontend must not submit `customValues`; BCS continues to generate it by
  comparing non-empty submitted values with stored `options[].value`.
- An empty answer contains no custom value, so it is valid regardless of
  `allowOther`.

Missing answer objects and missing `values` remain invalid. This preserves
question identity and distinguishes an explicit skip from a malformed request.

## Data Flow

BCS validates the complete answer map, classifies any non-empty option values,
and augments every answer with the stored `question` and optional `header`.
For `values: []`, the existing partition operation produces `values: []` and
no `customValues`. BaaS then preserves the empty list and serializes an empty
Engine selection row.

## Compatibility

The change restores the empty-array skip behavior previously supported by BCS
and already retained by BaaS. Existing non-empty declared and custom answers
are unchanged. No database, Provider method, or Engine wire-format migration is
required.

## Verification

- BCS service regression coverage submits two questions with one skipped and
  verifies the exact Provider resolution.
- BCS WebSocket contract coverage verifies that the public
  `interaction.resolve` request accepts the same shape.
- Existing BaaS tests continue to prove that an empty `values` list reaches the
  Engine resolution unchanged.
- Provider 2.0 documentation defines `values: []` as the sole skip encoding and
  keeps missing/non-string/blank values invalid.
