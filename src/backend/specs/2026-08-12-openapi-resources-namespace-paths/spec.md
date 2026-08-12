# Fix file addressing for OpenAPI bot resources

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

2. **Nothing validates the caller-supplied name.** The console path rejects
   parent-directory traversal and enforces a file extension allow-list. The
   public API path applies neither, and the name comes straight from a query
   parameter. Anchoring the address does **not** fix this on its own: a name
   that climbs out of the workspace still climbs out of the anchored address,
   because nothing between the caller and the write normalizes it.

3. **The endpoint has no test coverage.** It is recorded in the endpoint
   coverage baseline as having neither a success nor an error case.

A related consequence of the bare-name addressing: because every uploaded file
is treated as living at the top level, two files with the same name in different
directories collide, and the API rejects the second as a duplicate.

The same defect shows up differently depending on which runtime family a bot
uses. Bots whose runtime accepts an unanchored name store the file in the wrong
place and report success. Bots whose runtime already requires a properly
anchored address reject the upload outright, so the endpoint fails for them
today. Anchoring the address fixes both.

## Goals

**G1. Uploaded files land in the bot's workspace.** A file uploaded through the
public API is visible to the bot, and survives a container recycle.

**G2. A malformed or hostile name cannot write outside the workspace.** Rejected
with a clear client error.

**G3. Uploads can target a subdirectory.** The caller includes the directory in
the name; nested directories are created as needed. The
same-name-in-different-directories collision disappears as a consequence.

**G4. The resource listing reflects what is actually on the bot.** Directories
appear, and so do files the bot created itself — not only files that happen to
have a record in the database.

**G5. The endpoint is covered by tests** for both success and failure, replacing
its coverage-baseline entry.

## Non-goals

- **Changing the console path.** It works today. Migrating it needs a full
  regression pass and is deliberately excluded.
- **Changing the address format itself.** This change makes the public API
  compose the *same* address the console already composes. Moving both to a
  shorter, runtime-resolved address is a follow-up (see below).
- **Runtime-side containment.** A second line of defense behind the caller-input
  validation belongs in the runtimes, and is deferred with the address-format
  work because it needs the same cross-repository coordination.
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
| Upload name | any string accepted verbatim | traversal rejected; extension allow-list enforced |
| Subdirectories | not expressible | a name may carry a relative path; directories created as needed |
| Duplicate detection | same name anywhere collides | same name in different directories does not collide |
| Listing source | resource records only | actual workspace contents, joined to records |
| Listing contents | files with records | files and directories, including bot-created files |

Files listed from the workspace that have no corresponding record are returned
without a resource id. Making them fully addressable is out of scope; this is a
listing-visibility change only.

## Success criteria

1. A file uploaded through the public API is readable at the bot's workspace
   root, and appears in the bot's own view of its files.
2. A file uploaded with a name carrying a relative path lands at that path
   inside the workspace, with intermediate directories created.
3. Two files with the same leaf name uploaded under different directories both
   succeed.
4. A name containing a parent-directory reference is rejected with a client
   error and no write occurs.
5. A file extension outside the allow-list is rejected, matching the console
   path's behavior.
6. Listing a bot's resources shows directories and files present in the
   workspace, including at least one file the bot created itself, alongside
   link-type resources.
7. The endpoint's coverage-baseline entry is removed and replaced by real cases.

## Deferred to a follow-up

The original scope also moved the public API onto a shorter, runtime-resolved
address format, with the runtime bounding every write and rejecting unanchored
names outright. That work is sound and still wanted — it removes one of two
address-translation layers and makes this class of bug impossible rather than
merely fixed — but it requires a matching change in three runtimes that live in
a separate repository, plus a staged rollout in which the runtimes ship first.

Splitting it out lets the user-visible defect and the validation gap ship
immediately with no cross-repository dependency. What is given up in the interim
is defense in depth: caller-input validation becomes the only barrier, rather
than the first of two.

## Open questions

None blocking.
