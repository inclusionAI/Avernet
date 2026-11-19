# Run detail HTTP contract

Owner: `@avernet/workflow`. `GET /runs/:flowId` relative to the host API mount
(normally `GET /api/runs/:flowId`) is a read-only Service API for the workbench
and other authorized HTTP clients, not an engine Plugin API.

A successful response keeps the existing `{ run, nodes, availableInterventions }`
envelope. Runtime metadata is off by default and all three fields are omitted
on the standalone public mount. A host may set `exposeRuntimeMetadata: true`
in the 12th and final `createRunsRouter` argument, after the existing 11th
`runSubviews` argument (`null` when no reader is supplied). A host may opt in
only when it mounts verified run-view authorization before the router. That
guard must verify the caller's server-side identity and enforce the requested
run's per-workflow Bot/owner access. The option itself does not authorize requests.

For ordinary run detail on an opted-in mount, the router adds these flat,
required `RunRuntimeMetadata` fields to `run`:

| Response field | Stored run field | Type |
| --- | --- | --- |
| `device_id` | `CLIENT_ID` | `string \| null` |
| `bot_type` | `BOT_TYPE` | `string \| null` |
| `stage` | `STAGE` | `string \| null` |

Values come only from the requested run's stored credentials JSON object. Each
lookup uses an exact, case-sensitive, own top-level key. Nonblank strings are
returned as stored. Missing, null, blank, whitespace-only, or non-string values
return `null` independently. Missing, malformed, or non-object JSON (including
arrays) returns `null` for all three fields. No values are inferred or defaulted.
The raw `credentials_json` and all other values from it are not returned or
logged by this projection. The subworkflow reader does not select credentials;
its ordinary run details therefore receive `null` values if opted in, with no
additional credential read. Managed fanout parents and sensitive fanout
children keep their existing reader-backed redaction, including omission of
all three fields even on an opted-in mount.

Without the reader, only the new metadata is withheld when the stored state
object has either exact own top-level marker (`subworkflowFanouts` or
`fanoutOrigin`), regardless of the marker value. Malformed or non-object
state also withholds the metadata; a SQL `NULL` or empty state column counts as
absent. The existing readerless response fields and status are unchanged.

## Compatibility

These fields are additive and detail-only on opted-in mounts. The default
response and [run list](run-list-contract.md) are unchanged.
Where already present, `run.hasCredentials` still reports whether the stored
column is neither null nor the empty string; it does not indicate valid JSON
or the presence of metadata. No storage or deployment migration is required.
Clients should treat absent metadata fields the same as `null`.

`server/routes/__tests__/run-detail-runtime-metadata.test.ts` exercises both
mount modes through Express and the current migrated SQLite schema.

## Run scope and session updates

A guarded host may pass its authorized workflow and originating Bot snapshot
to the session PATCH handler via request-local server state. The write is then
conditional on both values still matching. Requests whose scope no longer
matches receive a denial; clients cannot supply the expected snapshot. Mounts
without a server-provided snapshot keep their existing session behavior.
