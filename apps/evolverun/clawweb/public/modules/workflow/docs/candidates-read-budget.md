# Candidate list reads and frozen input budgets

`GET /api/workflow-repairs/candidates` and the view-authorized
`POST /api/workflow-repairs/candidates/query` use the same read service. Source
structure is validated before presentation, but a source record's accumulated
diagnostic context is not a frozen generation task. A single proposal supported
by many runs can exceed 1 MiB and must remain discoverable in these reads.

The `workflow-repair/v2` list adds an optional `readSummary` marker to large
originals and to plan members. Display wording is bounded by UTF-8 bytes. The
marker carries complete source/run/diagnosis counts, original byte size and
bounded operation comparison identities. It explicitly means full proposal and
evidence bodies are omitted from this display projection. Item IDs, proposal
keys, content revisions, lifecycle state and all plan member IDs remain intact.
An incomplete operation comparison is conservative for multi-selection; the
server still validates the complete selection against original proposals.

Plans retain all members and one plan selection expands into every original ID.
`GET /items/:itemId?workflowId=...&originalOnly=true` reads that member's full
proposal and hydrated evidence without attaching the whole plan again. The UI
loads it only on an explicit full-evidence action. The existing item detail read
also retains the selected member's full body and the plan's member summaries.

List pages obey both requested row count and a byte budget. Page numbers and
`totalPages` refer to these complete-row pages; a page never clips part of a
plan. Optional issue previews use only the remaining response budget and always
retain their exact total. Empty preview items do not mean an empty issue.

Tasks, signature keys and facets have independent `metadataPage` pagination (50
entries per collection). `metadata` includes complete collection counts,
`totalPages` and a digest. The browser API assembles all metadata pages and fails
the read if source or metadata digests change during assembly. External callers
must follow this metadata pagination separately from item pagination.

The legacy server-only `rawItems` query remains an unprojected trusted read for
automatic selection. New automatic selection resolves selected original IDs
through `items()` before inspecting diagnostic severity or freezing a task.
Neither path freezes the browser's summary. Original proposal/evidence reads,
source-change detection and generation remain server-owned.

The POST query body limit remains 256 KiB, the list response limit remains 4 MiB,
the full item response limit remains 8 MiB and generation item/batch limits remain
1 MiB. Large frozen input is rejected before dispatch instead of silently
truncating evidence or generating from the display summary. The structural read
validator does not relax the generation validator's instruction/source contract.

Payload errors retain `code=PAYLOAD_TOO_LARGE`, `limit` and `actualBytes`, and add
safe phase information: `request_body`, `candidates_response`, `item_response`,
`frozen_item` or `frozen_batch`, plus a `reasonCode` and request ID. The route logs
only these diagnostics, not request/source text. The UI displays recognized phase
and byte diagnostics; a proxy/parser 413 without this JSON is marked as an unknown
limit origin instead of being attributed to source size.

Local conformance tests compose a real source adapter, workbench, router and
temporary SQLite database. They cover GET/POST reads with accumulated diagnoses,
complete details/counts, source selection/frozen originals, real oversized request
bodies, metadata paging, mixed full/summary conflicts and multibyte display text.
These checks do not assert the contents of a particular preproduction record or
that a deployed service has adopted the fix.
