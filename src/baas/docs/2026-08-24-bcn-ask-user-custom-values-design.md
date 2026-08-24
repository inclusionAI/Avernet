# BCN Ask-User Custom Values Design

## Problem

BCN currently places both declared option values and free-form "other" input in
`answers.<questionId>.values`. BaaS therefore cannot distinguish their meaning
without guessing. Passing a free-form string through Engine `selectedOptions`
fails Engine's canonical-option validation and leaves the interaction unresolved.

## Contract

Frontend keeps its existing values-only request. It puts declared option
selections and free-form input together in `values`:

```json
{
  "answers": {
    "tradeoff": {
      "values": ["sensory", "less sugar"]
    }
  }
}
```

BCS compares those values exactly with the requested question's
`options[].value`. It sends declared selections to the Provider in `values` and
free-form selections in `customValues`:

```json
{
  "answers": {
    "tradeoff": {
      "values": ["sensory"],
      "customValues": ["less sugar"],
      "header": "Preference",
      "question": "Which trade-offs matter?"
    }
  }
}
```

The Provider-facing `values` and `customValues` are independent and may both be
non-empty for a multi-select question. For a question with options, missing
`allowOther` means true. An explicit `allowOther=false` rejects any Frontend
value that does not exactly match a declared option; BCS returns a clear
`invalid_request` response and logs the BCS run, Provider run, session, group,
bot, interaction, and resolver identifiers. BCS remains the authority for
`header` and `question` and overwrites client-supplied copies from the stored
requested interaction.

BaaS requires non-empty `header` and `question` for every submitted answer. It
does not infer custom input from an unknown `values` entry and does not derive
`header` from `questionId`.

## Engine Mapping

BaaS renders each answer in requested question order:

- declared values remain unchanged in the rendered text;
- custom values become `自定义输入: <value>`;
- rendered pieces are joined with `，`, and question summaries with `；`;
- when `customValues` is empty, Engine `selectedOptions` is the declared
  `values` row;
- when `customValues` is non-empty, Engine `selectedOptions` is `["other"]`.

The last rule is required because the current Engine rejects synthetic `other`
combined with declared options. For a mixed answer, the declared selections are
therefore retained in `answer`, `message`, `values`, and `answers`, while the
Engine selection row is the synthetic `other` marker.

## Compatibility and Persistence

The Frontend contract remains values-only. `customValues` is an internal
BCS-to-Provider field, so Frontend does not need an additional input control or
sentinel. No database migration is required. BCS stores the normalized
resolution as JSON, and BaaS maps it into its existing durable Engine-resolution
shape.

BaaS does not perform fallback classification: it consumes the explicit
`values/customValues` split produced by BCS.

## Validation

Contract and unit tests cover ordinary selections, custom-only answers, mixed
multi-select answers, missing `allowOther`, rejected custom values when
`allowOther` is explicitly false, correlated warning logs, Frontend error
propagation, BCS metadata augmentation, BaaS boundary parsing, and exact Engine
request output.
