# Namespace-relative addressing for OpenAPI bot resources

Tracking issue: [inclusionAI/Avernet#1000](https://github.com/inclusionAI/Avernet/issues/1000)

## Problem

Files uploaded through the public resources API never reach the bot's workspace.
The upload reports success and a resource record is created, but the bytes land
somewhere the bot cannot read and that is not preserved when the bot's container
is recycled. Nothing in the request, the response, or the logs indicates a
failure.

This was confirmed on a pre-environment bot: a 3-byte upload returned `201
Created` with a resource id, and the file existed at exactly one location on the
container — a directory belonging to the runtime, not to the bot.

Three defects sit behind this, all on the same code path:

1. **The file is addressed by a bare name.** The console upload path and the
   public API path compose file addresses differently. The console path builds a
   complete, bot-scoped address; the public API path sends only the filename. A
   bare name has no anchor, so the runtime resolves it against whatever
   directory it happens to be running from.

2. **Nothing validates the caller-supplied name.** The console path strips
   directory components, rejects parent-directory traversal, and enforces a file
   extension allow-list. The public API path applies none of these, and the name
   comes straight from a query parameter. The runtime does not bound the write
   either, so a traversing name is written wherever it points.

3. **The endpoint has no test coverage.** It is recorded in the endpoint
   coverage baseline as having neither a success nor an error case.

A related consequence of the bare-name addressing: because every uploaded file
is treated as living at the top level, two files with the same name in different
directories collide, and the API rejects the second as a duplicate.

## Goals

**G1. Uploaded files land in the bot's workspace.** A file uploaded through the
public API is visible to the bot, and survives a container recycle.

**G2. A malformed or hostile name cannot write outside the workspace.** Rejected
with a clear client error, and bounded a second time by the runtime so that a
future caller mistake cannot escape either.

**G3. Uploads can target a subdirectory.** The caller names a directory
explicitly; the same-name-in-different-directories collision disappears as a
consequence.

**G4. The resource listing reflects what is actually on the bot.** Directories
appear, and so do files the bot created itself — not only files that happen to
have a record in the database.

**G5. A misconfigured or mid-rollout runtime fails loudly** rather than silently
storing a file in the wrong place and reporting success.

**G6. The endpoint is covered by tests** for both success and failure, replacing
its coverage-baseline entry.

## Non-goals

- **Changing the console path.** It works today. Migrating it needs a full
  regression pass and is deliberately excluded.
- **Retiring the existing address format.** Both formats stay supported. The old
  one is not deprecated in this change.
- **Extending the new addressing to identity or engine-config files.** Those
  namespaces are reserved so they cannot silently misbehave, but they keep using
  the current addressing.
- **Folder records.** Creating a folder as a first-class resource stays
  unsupported; directories are physical only.
- **Correcting historical records.** Files already written to the wrong location
  are handled separately by the issue owner.
- **Changing how a resource is addressed by callers.** Resources are still
  identified by resource id; this is not a move to path-based addressing.

## Behavior changes

| Area | Today | After |
|---|---|---|
| Upload destination | runtime's working directory | bot's workspace |
| Upload name | any string accepted verbatim | filename only; a name containing a path separator is rejected |
| Upload target directory | not expressible | optional caller-supplied directory |
| Duplicate detection | same name anywhere collides | same name in different directories does not collide |
| Listing source | resource records only | actual workspace contents, joined to records |
| Listing contents | files with records | files and directories, including bot-created files |
| Runtime that does not understand the new address | silent success, file misplaced | request fails, no record created |

Files listed from the workspace that have no corresponding record are returned
without a resource id. They can be seen and downloaded is out of scope for them;
this is a listing-visibility change only.

## Success criteria

1. A file uploaded through the public API is readable at the bot's workspace
   root, and appears in the bot's own view of its files.
2. The same upload targeted at a subdirectory lands in that subdirectory, which
   is created if absent.
3. Two files with the same name uploaded to different directories both succeed.
4. A name containing a path separator, and a directory containing a
   parent-directory reference, are both rejected with a client error and no
   write occurs.
5. A file extension outside the allow-list is rejected, matching the console
   path's behavior.
6. Listing a bot's resources shows directories and files present in the
   workspace, including at least one file the bot created itself, alongside
   link-type resources.
7. Against a runtime that has not been updated, an upload fails with a server
   error and no resource record is created.
8. The endpoint's coverage-baseline entry is removed and replaced by real cases.

## Open questions

None blocking. Two decisions were made rather than asked, and are called out in
the plan: the query parameter name for the target directory, and the addition of
optional path fields to the resource schema so directories can be represented.
