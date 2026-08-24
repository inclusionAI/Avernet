# BCN Ask-User Custom Values Design

## Problem

BCN currently places both declared option values and free-form "other" input in
`answers.<questionId>.values`. BaaS therefore cannot distinguish their meaning
without guessing. Passing a free-form string through Engine `selectedOptions`
fails Engine's canonical-option validation and leaves the interaction unresolved.

## Contract

Each BCN ask-user answer keeps declared option selections in `values` and may
add free-form selections in `customValues`:

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

`values` and `customValues` are independent and may both be non-empty for a
multi-select question. BCS accepts `customValues` only for a requested question
with `allowOther=true`; it validates every `values` entry against the declared
options. BCS remains the authority for `header` and `question` and overwrites
client-supplied copies from the stored requested interaction.

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

The change is additive on BCN Provider 2.0: existing requests containing only
`values` keep their current behavior. No database migration is required. BCS
stores the resolution as JSON, and BaaS normalizes the incoming fields into its
existing durable Engine-resolution shape.

Malformed or unmarked off-list values are not reclassified as custom input.

## Validation

Contract and unit tests cover ordinary selections, custom-only answers, mixed
multi-select answers, rejected custom values when `allowOther` is false, BCS
metadata augmentation, BaaS boundary parsing, and exact Engine request output.
