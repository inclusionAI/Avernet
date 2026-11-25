# AIStudio training watch: browser status and diagnostics

ClawWeb projects the persisted watch into the existing browser DTO for
`GET /api/runs/:flowId/nodes/:nodeId/aistudio-train` and the successful manual
poll response. The [projection](../server/contracts/aistudio-train.ts) does not
query AIS or change the node, workflow, or watch phase.

- `lastStatus` preserves allowlisted AIS statuses, including the AIS failure
  value `error` (matched case-insensitively). Other non-null status text is
  redacted to `unknown`; `null` remains `null`.
- `lastError` is a display-only watch diagnostic. The exact watcher message
  `AIStudio status unknown` becomes `AIStudio returned an unrecognized status`:
  AIS returned a status the watcher could not interpret. Other non-null
  diagnostics retain the redacted `AIStudio status query failed` fallback;
  arbitrary diagnostic strings are never returned. `null` remains `null`.
- The card displays AIS status, watch phase, and server-owned node status
  separately. A watch diagnostic does not assign a node outcome.
