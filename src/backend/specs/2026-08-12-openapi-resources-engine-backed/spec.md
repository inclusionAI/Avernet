# Engine-backed file resources for the OpenAPI bot resources API

Tracking issue: [inclusionAI/Avernet#1000](https://github.com/inclusionAI/Avernet/issues/1000)

## Problem

Files uploaded through the public resources API never reach the bot's workspace.
The upload reports success and a resource record is created, but the bytes land
somewhere the bot cannot read and that is not preserved when the bot's container
is recycled. Nothing in the request, the response, or the logs indicates a
failure. Confirmed on a pre-environment bot: a 3-byte upload returned `201
Created` with a resource id, and the file existed at exactly one location on the
container — a directory belonging to the runtime, not to the bot.

Three defects sit behind the misplacement:

1. **The file is addressed by a bare name.** The console path builds a complete,
   bot-scoped address; the public API path sends only the filename. A bare name
   has no anchor, so the runtime resolves it against whatever directory it
   happens to be running from.
2. **Nothing validates the caller-supplied name.** The console path rejects
   parent-directory traversal and enforces an extension allow-list; the public
   API path applies neither, and the name comes straight from a query parameter.
   Anchoring the address does not fix this on its own — a name that climbs out of
   the workspace still climbs out of the anchored address.
3. **The endpoint has no test coverage.** It is recorded in the endpoint
   coverage baseline as having neither a success nor an error case.

Underneath those sits a structural problem that the misplacement merely exposed:
**the database record, not the bot's filesystem, is treated as the truth about
which files exist.** A record can point at bytes that were never written where
they claim to be — which is exactly what happened. The inverse is equally
broken: a file the bot creates itself has no record, so the API cannot see it,
list it, or serve it, even though it is plainly there.

The console has already worked through this. Its file operations are addressed
by path and read the workspace directly, and its record-addressed download is
explicitly marked legacy in favour of the path-addressed one. The public API is
still on the older model.

## Goals

**G1. The bot's filesystem is the source of truth for files.** Every file
operation resolves against the workspace, so what the API reports and what the
bot sees cannot diverge.

**G2. Uploaded files land in the bot's workspace** and survive a container
recycle.

**G3. Files the bot creates are first-class.** They can be listed, downloaded,
previewed, and deleted through the API without any record existing.

**G4. A malformed or hostile name cannot write outside the workspace.** Rejected
with a clear client error.

**G5. Uploads can target a subdirectory.** The caller includes the directory in
the name; nested directories are created as needed. The
same-name-in-different-directories collision disappears as a consequence.

**G6. No runtime read consults a file record.** *(Revised mid-implementation.)*
Originally the record was to enrich responses with what the filesystem cannot
know — uploader, upload time, upload-vs-bot-created. That was dropped: since a
bot generates files itself, there is often no uploader to report, and a response
field present for some files and absent for others is worse than absent for all.
The record is still written, but for exactly one consumer — the publish pipeline,
which builds a released bot's manifest from it and would otherwise lose files
silently. Every field of a file in a response now comes from the workspace.

Because that one consumer is load-bearing, the write is not best-effort: an
upload whose record cannot be written rolls the file back and fails. Reporting
success would publish a bot silently missing that file, and the obvious repair —
uploading it again — cannot work, since the file would be on disk and the
duplicate check would answer 409.

**G7. Link resources are unaffected.** A link has no file; it remains
record-backed and record-addressed.

**G8. The endpoint is covered by tests** for both success and failure, replacing
its coverage-baseline entry.

## Behavior changes

| Area | Today | After |
|---|---|---|
| Source of truth for files | database record | bot's filesystem |
| Upload destination | runtime's working directory | bot's workspace |
| Upload name | any string accepted verbatim | traversal rejected; extension allow-list enforced |
| Subdirectories | not expressible | a name may carry a relative path; directories created as needed |
| Duplicate detection | same name anywhere collides | same name in different directories does not collide |
| Listing | records only | workspace contents, enriched from records where one exists |
| Bot-created files | invisible to the API | fully usable |
| File addressing | by record id | **by path** |
| Link addressing | by record id | unchanged |

### Contract change

Files are addressed by path rather than by record id, so four operations change
shape: download, preview, delete, and single-resource lookup. This mirrors the
console, whose file operations are already path-addressed.

Record ids remain the addressing scheme for links. Record ids that previously
referred to files stop resolving.

This is a breaking change to a surface that is **not yet exposed to external
callers** — the router carries a standing "NOT PUBLIC-READY" gate pending the
auth workstream. Making the change now costs a test update; making it after
exposure would cost a deprecation cycle.

## Non-goals

- **Changing the console path.** It works today and is deliberately excluded.
- **Changing the address format on the wire.** The public API will compose the
  same address the console already composes. Moving both to a shorter,
  runtime-resolved address is a follow-up (below).
- **Runtime-side containment.** A second line of defense behind caller-input
  validation belongs in the runtimes and is deferred with the address-format
  work, since it needs the same cross-repository coordination.
- **Migrating historical records.** Records for previously uploaded files become
  inert; the issue owner handles them separately.

## Success criteria

1. A file uploaded through the public API is readable at the bot's workspace
   root and appears in the bot's own view of its files.
2. A file uploaded with a name carrying a relative path lands at that path
   inside the workspace, with intermediate directories created.
3. Two files with the same leaf name uploaded under different directories both
   succeed.
4. A name containing a parent-directory reference is rejected with a client
   error and no write occurs.
5. A file extension outside the allow-list is rejected, matching the console.
6. A file created by the bot — never uploaded — can be listed, downloaded,
   previewed, and deleted through the API.
7. Deleting a file through the API removes it from the workspace, and a
   subsequent listing does not show it.
8. Listing shows directories, uploaded files, and bot-created files, alongside
   link resources.
9. ~~An uploaded file reports its uploader and upload time~~ — **superseded by
   G6.** No file reports an uploader, a source, or timestamps: all files are
   reported identically from the workspace, with an empty `resource_id`. The
   uploader is still recorded, and the console still surfaces it from the shared
   table; this API simply does not.
10. ~~The coverage-baseline entry is removed and replaced by real cases~~ —
    **not achievable on this surface.** The endpoint-case runner authenticates
    with `x-user-id` while `/openapi/v1` requires a gateway-signed principal and
    the harness has no minter, so a case could assert nothing but a 401. The new
    routes are baselined for that documented reason, tracked by #651, and are
    covered by handler tests instead.

## Deferred to a follow-up

Moving the wire format to a short, runtime-resolved address, with each runtime
bounding every write and rejecting unanchored names outright. That work removes
one of two address-translation layers and makes the misplacement class of bug
impossible rather than merely fixed, but it requires matching changes in three
runtimes in a separate repository plus a staged rollout in which the runtimes
ship first.

What is given up in the interim is defense in depth: caller-input validation is
the only barrier against traversal, rather than the first of two.

## Known limitation: the duplicate check is not atomic with the write

Two uploads racing on the same absent path can both pass the occupancy check
before either writes, and the engine's write is an unconditional overwrite — so
both answer 201, two rows are written, and only the later bytes survive.

This is unchanged in kind from before: the previous code asked the record table
the same question with the same gap between asking and writing. What this change
alters is only *who* is asked, from a table that could disagree with the disk to
the disk itself.

It is not closed here because neither remedy exists at this seam.
`DeviceFileSystem` has no conditional-create — `write_file` is a plain write, and
every provider implements it as one — so there is nothing to make the check and
the write one operation. Serializing per path would need a lock spanning backend
replicas, which this service has no such facility for, and taking one per upload
path would be a significant piece of infrastructure for a race that costs a
last-writer-wins overwrite between two callers deliberately targeting the same
path.

The real fix is an exclusive-create flag on the engine's write API, which makes
the check redundant rather than better-timed. That is engine-side work in the
same repository as the wire-format change above, and belongs with it.

## Open questions

None blocking.
