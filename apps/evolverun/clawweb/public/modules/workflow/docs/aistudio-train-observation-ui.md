# AIStudio training observation UI

The run node row and expanded observation card display training status and the
server-owned automatic-query schedule. Neither view exposes a manual-query
button, pending-command feedback, manual cooldown field, or a manual-query
availability notice. Automatic observation refresh, GET retry, and historical
query-source information remain unchanged. Existing manual-query service APIs
remain backward-compatible; hiding browser controls does not change their
authorization or mailbox semantics.

The expanded card places its external task link immediately after the record ID
copy button. Links open in a new tab with `noopener noreferrer`. Missing or unsafe
URLs do not produce links; the browser never assembles a provider URL.

## Host-provided canonical links

`createAIStudioTrainRuntime` and the public router accept an optional synchronous
`resolveDetailUrl(recordId)` presentation provider. When supplied, it selects the
canonical task-detail URL from the authorized, server-loaded record ID, taking
precedence over a recorded log URL. The returned value must pass the existing
HTTPS/no-credentials URL sanitizer. A null or unsafe result produces no link.
Without a provider, the recorded safe URL (or null) remains unchanged.

The same response-only projection applies to GET and accepted manual-command
responses. It never modifies the durable watch, increments its persisted
version, queries the training provider, or changes the polling schedule.
Enterprise URL formats remain in the enterprise host adapter, not public code.
