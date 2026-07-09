# Teclaw Config Artifact Offloading for Large Bots

## Summary
When a teclaw bot is built and published, the backend saves the bot's full
configuration artifact into a publish record and reads it back later to boot the
bot. For richly-configured bots this artifact can grow past the size limit of
the database field it is stored in, causing the save to fail or silently
truncate. This feature keeps large artifacts out of that field by storing their
content in object storage and keeping only a small, self-describing reference in
the record — so bots of any configuration size can be published and booted
reliably.

## Motivation
The configuration artifact is persisted as JSON inside a single database field
that has a hard byte-size cap (~64 KB). The artifact bundles the bot's resolved
skills, resources, identity files, MCP servers (with inlined credentials), and
an opaque engine-owned blob. As bots accumulate configuration, real artifacts
have started to approach and exceed this cap. When that happens today the write
either errors out or is truncated, which breaks publishing and can corrupt a
bot's boot configuration. We need publishing to be robust regardless of artifact
size, without a risky schema migration of the existing field.

## User Stories
- As a bot owner, I want to publish a heavily-configured teclaw bot without
  hitting an opaque failure, so that my bot deploys the same way small ones do.
- As a backend engineer, I want oversized artifacts handled transparently by the
  storage layer, so that the many code paths that read and write the artifact do
  not each need to know about the size workaround.
- As an operator debugging a publish record, I want a record whose artifact was
  offloaded to clearly say so and point to where the content lives, so that I can
  understand and trace the data without guessing.

## Acceptance Criteria
- [ ] A bot whose serialized artifact exceeds the configured size threshold can
      be built, published, upgraded, and booted with no size-related failure.
- [ ] Small artifacts (under the threshold) are stored exactly as they are today
      — no object-storage round trip, no behavior change.
- [ ] Callers that read the publish record always receive the full artifact,
      whether it was stored inline or offloaded — the offload is invisible to
      them.
- [ ] When an artifact is offloaded, the stored record carries a clearly-named
      marker indicating (a) that offloading happened, (b) where the content is,
      and (c) the original size and threshold, plus a short human-readable note.
- [ ] Deleting a publish record also removes its offloaded artifact content, so
      object storage does not accumulate orphans.
- [ ] Repeated writes of the same record (e.g. re-stamping during the publish
      flow) do not accumulate stale offloaded copies.
- [ ] The threshold is a single, documented value with headroom below the field
      cap so other data in the same field still fits.

## In Scope
- Size-based decision to store the artifact inline vs. in object storage.
- Transparent re-inlining of offloaded artifacts on read.
- A self-describing reference marker for offloaded artifacts.
- Cleanup of offloaded content when a publish record is deleted.
- The ability to read an object back out of object storage (a read capability
  the storage abstraction does not currently expose).

## Out of Scope
- Migrating or changing the type of the existing database field.
- Backfilling / rewriting artifacts already stored in existing records.
- Compressing or trimming the artifact's contents to make it smaller.
- Changing the published artifact contract that external engines consume (the
  offload marker is an internal storage detail, never surfaced to the engine).
- Offloading any other large field besides the config artifact.

## Resolved Decisions
- **Threshold value:** ~60 KB (leaving ~4 KB headroom under the 64 KB field cap
  for sibling data in the same field). *(accepted)*
- **Missing read capability on a deployment:** if the object-storage
  implementation does not expose a read method, disable offloading entirely and
  store inline (old behavior), logging a clear warning. This guarantees no
  runtime failure on deployments whose out-of-tree implementation has not yet
  added the read method; the size fix activates automatically once it does.
  *(accepted)*
- **Offload failure at write time:** fail loudly rather than silently fall back
  to an inline write that would hit the field cap and truncate. *(accepted)*
