# Aix Run List WorkItem Wire Contract

**Contract version:** 1  
**Consumer:** `RunStatusService.get_session_issues`  
**Producer:** `aix run list --filter <workspace_root> --json`

This document records the subset of the `aix run list` JSON payload that
Avernet relies on when it builds the existing session-issues API response.
The upstream CLI may return additional fields; fields not listed here are
ignored by this consumer.

## Top-level payload

```json
{
  "runs": [
    {
      "runId": "r-...",
      "id": "r-...",
      "startedAtUnixMs": 1790058362860,
      "updatedAtUnixMs": 1790058650134,
      "projectDir": "/absolute/project/path",
      "workItem": {
        "provider": "work-item",
        "site": "example",
        "type": "req",
        "id": "2026091700119169408",
        "url": "https://example.com/work-items/2026091700119169408",
        "title": "Work item title",
        "subject": "Fallback title"
      }
    }
  ]
}
```

## Consumer rules

- `runs` must be an array. A missing, empty, or non-array `runs` value produces
  no issue items.
- Each run entry must be a JSON object.
- A run contributes an issue only when `workItem` is an object and
  `workItem.url` is present and non-empty. Other runs are ignored.
- `runId` defaults to `run.id` when `run.runId` is absent.
- `at` is set from `run.startedAtUnixMs`, or from
  `run.updatedAtUnixMs` when the start timestamp is absent.
- `title` is set from `workItem.title`, or from `workItem.subject` when
  `title` is absent.
- `provider`, `url`, `title`, and the run metadata are copied into the
  existing issue response shape; `workItem` itself is not exposed anew.
- If the command fails or its stdout is not valid JSON, the service raises
  `AixCommandError`. The HTTP adapter maps that domain error to HTTP 500.

## Compatibility rule

A breaking upstream change is any change that removes `runs`, changes it from
an array, changes `workItem` from an object to another shape, removes
`workItem.url` for runs that should still produce issues, or changes the
timestamp fields from integer-valued milliseconds. The consumer contract test
must be updated in the same change as such an upstream update.
