# Content store on shared object storage

## Summary

The platform keeps its own copy of every piece of bot content it has fetched,
captured or produced (persona files, skills, resources, memory, scripts),
addressed by content hash. Today those bytes live on the local disk of
whichever backend instance wrote them, while the record that they exist lives
in the shared database. This feature moves the bytes to the deployment's shared
object storage, so any backend instance can read what any other instance wrote
and the content survives an instance being replaced.

## Motivation

Found on the pre environment on 2026-11-10: a genome revision was captured,
and reading its content back returned `404 Not found`. The database held the
record, but the bytes were on a different instance's container disk. The same
fault affects more than reads:

- **Genome content reads** fail on every instance except the one that captured.
- **Promoting a captured revision** fails unless the apply task lands on the
  capturing instance.
- **Re-applying a Manifest** with inline content, skills or directory
  resources fails when the task runs on a different instance from the first
  apply: the record exists, so the bytes are not written again, and they are
  not on this instance's disk.
- **The "keep last copy" fallback** during a source outage cannot find the
  last copy from another instance.
- **Replacing an instance loses its content permanently**, although the
  platform's retention rule for this store is "never delete".

The store's design already assumed a shared volume would be configured per
deployment; nothing enforced it, and the neutral default is a relative local
path.

## User Stories

- As a bot owner, I want to read the content of any revision I captured, so
  that I can inspect and diff what my bot holds, whichever instance answers.
- As a bot owner, I want promoting a captured revision and re-applying a
  Manifest to succeed regardless of which instance runs the work.
- As an operator, I want stored bot content to survive a deploy or an instance
  replacement, without mounting or configuring a shared volume.
- As a community (single-machine) user, I want an upgrade to keep reading the
  content my installation already stored, with no manual step.

## Acceptance Criteria

- [ ] Content written by one backend instance is readable by any other
      instance sharing the same database and object storage: genome content
      read, revision diff, promotion, Manifest apply and the keep-last
      fallback all succeed across instances.
- [ ] Content written before an instance is replaced is still readable
      afterwards.
- [ ] A read still returns only bytes that hash to the requested address; a
      mismatch is reported as a platform fault, never returned.
- [ ] A missing blob is still reported as missing (not as a fault), and an
      unreachable object storage is reported as a platform fault (not as
      missing) wherever the storage can tell the two apart.
- [ ] A write that cannot be stored fails the operation; no record is filed
      for bytes that were not stored.
- [ ] Re-applying a Manifest or re-capturing a bot whose record exists but
      whose bytes are missing restores the bytes (self-heal), for inline
      content, built trees, fetched files and captured content alike.
- [ ] Content already stored on an instance's local disk before the upgrade
      is still readable on that instance after it, and is copied to object
      storage the first time it is read there.
- [ ] A single-machine installation works with no object-storage service: its
      default configuration stores content on local disk as before.
- [ ] Stored content is never deleted or overwritten with different bytes.

## In Scope

- Storing and reading content-store bytes through the deployment's object
  storage, under a dedicated area of the existing bucket.
- Self-heal of missing bytes on re-apply and re-capture.
- Reading through to content left on local disk by earlier versions.
- Configuration and documentation of where content is stored.

## Out of Scope

- Changing what is recorded in the database, the address format, or the
  retention rule.
- Recovering content from instances that no longer exist (it must be
  re-fetched by a re-apply or re-captured).
- Splitting directory content into per-file objects (the recorded follow-up to
  the "one tree, one blob" decision).
- A local read cache in front of object storage.
- Changing how other features use object storage.

## Open Questions

- Does the object-storage bucket used by pre/prod have a lifecycle (expiry)
  rule that would apply to the new area? The store must never expire. To be
  confirmed by the operator before rollout; it does not change the design.
- Which storage does each internal deployment's object-storage binding
  provide to this store by default? The plan assumes the existing binding;
  the internal overlay may need one line to opt a deployment in or out.
