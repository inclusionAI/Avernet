---
status: accepted
---

# Delegate Bot-local Skill package deletion to the runtime

Bot-local Skill deletion keeps authorization, edit leasing, reference checks, and metadata deletion in Backend, but delegates removal of the complete content root to one runtime operation. Backend does not list, copy, verify, restore, or individually remove package files: Local, BaaS, and ARCA use their existing recursive-delete adapters, while Teclaw maps the same Backend package-deletion interface to its existing `/api/v1/file/remove` endpoint. For HTTP adapters, only a positively identified success permits metadata deletion. Teclaw accepts only 2xx because its 404 cannot distinguish an absent target from an unavailable route; 404, other HTTP failures, timeouts, and unknown outcomes fail closed without a fallback protocol.

## Consequences

The former `.delete-<uuid>` quarantine and Backend file-repair choreography are removed. Runtime files and Backend metadata do not share a transaction: a metadata failure or a concurrent new reference after successful runtime deletion can leave metadata or references whose package is absent, and an already-removed Teclaw target is not automatically treated as success. This consistency gap is accepted for this release; it must not be described as cross-storage atomic deletion. Teclaw recursive-directory support remains a post-deployment validation item rather than a release gate.

## Alternatives and controls

Keeping Backend quarantine was rejected because it requires Backend to enumerate, copy, verify, restore, and purge runtime-owned files. A durable saga or outbox with explicit repair state would close more crash windows while preserving ownership, but requires a larger cross-store lifecycle and is deferred. The selected bounded exception uses the existing edit lease, performs reference checks before the runtime call and again inside the metadata transaction, requires a positive runtime result, returns `LocalSkillStorageError` for every post-delete metadata conflict, and never retries an unknown runtime outcome through another protocol. Skill Center maintainers own this decision; Teclaw recursive deletion is verified on the first deployment of this change. No architecture boundary waiver is required because the runtime remains the file owner and Backend retains only domain policy and metadata ownership.
