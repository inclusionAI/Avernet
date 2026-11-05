# Restore routine execution conversation details

This is an additive repair to the legacy Cron history migration. The existing
GET `/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs` permission gate,
owner addressing, runtime_stage/device_uuid/operator_id selection and pagination
remain unchanged. No Service API, Plugin API or storage change is required.

Each RoutineRun additionally returns `input`, `output`, `error` (nullable text)
and `messages` (an ordered array of `{role, content}` text entries, default `[]`).
Normalize the first nonempty usable array from legacy `messages`, `conversation`,
or `turns`; text comes from `content`, `text`, or `output`. Ignore non-object
entries and non-text payloads, and never stringify arbitrary provider objects.
Only these declared fields are projected; authentication/provider extensions
are not exposed. Missing role defaults to assistant. Missing content is not
invented. Run-level input overrides enclosing response input; each fan-out
target's enclosing input is isolated to its own runs.

Bot management and MyTask instance/history views retain these fields and share a read-only conversation drawer
for each history row. It uses that row's own stage, instance and Caller. When
messages exist, render them in order; otherwise render reported input/output.
Always show reported errors, including alongside partial messages. If nothing
is reported (including older backend versions), show an explicit empty state.
Do not use the current routine prompt as historical evidence. Render content
as escaped React text, never HTML. Opening details makes no additional request
and grants no new chat or edit access. Updating the selected task/page unmounts
old row drawers. Added transcript fields do not expand existing relay logs.

Validation covers single-target and fan-out HTTP responses, legacy aliases,
malformed content, field allowlisting, older responses, accurate addressing,
two rows with identical run IDs, errors, empty states, close/reopen and HTML
escaping. Public OpenAPI is derived from these Pydantic schemas; consumers must
regenerate from the updated schema. Rollback drops the additive projection and
drawer without changing persisted records.
