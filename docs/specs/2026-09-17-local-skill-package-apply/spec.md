# Local Skill Package Apply Phase 1

Status: implementation authorized for `REL20260917`.

Owning modules: Backend Skill Center and Engine Skills Service. OCB provides
the corporate Engine implementations and production transport; Aix Relay and
Teclaw remain separately deployed consumers of the same semantic contract.

## Problem

Bot-local Skill create/replace currently expands a validated ZIP in Backend and
then performs directory cleanup plus one HTTP upload per file. Backend thereby
owns Runtime filesystem orchestration, and Teclaw cannot implement the same
temporary-directory choreography through its file API.

## Contract

Standard Engines expose:

```text
POST /api/skills/local/apply
Content-Type: multipart/form-data

skill_name=<safe manifest name>
layout=LEGACY|POOL
file=<complete canonical ZIP>
```

The active Engine declares `skills.local_package.apply.v1`. A successful
standard response uses the Engine `ApiResponse` envelope:

```json
{
  "success": true,
  "data": {
    "skill_name": "weather",
    "action": "created|replaced|unchanged",
    "content_digest": "sha256:<64 lowercase hex>",
    "target_path": "/optional/diagnostic/path"
  }
}
```

`target_path` is diagnostic only. Backend never derives or persists a locator
from it. The digest is the SHA-256 of the exact canonical ZIP bytes sent by
Backend and must match before Backend writes metadata.

Known failures use `success=false`, machine-readable `error`, and `message`.
The stable errors are `invalid_package`, `package_too_large`,
`publish_in_progress`, `publish_lock_unavailable`, `publish_failed`, and
`rollback_failed`.

Teclaw uses its provider wire without a capability probe:

```text
POST /api/v1/file/skill-package
x-target-bot-id: <bot_id>
skills_name=<skill_name>
file=<complete canonical ZIP>
```

Backend normalizes Teclaw's flat response and bare `sha256` into the standard
domain result.

## Backend selection and compatibility

- Teclaw always calls the new provider endpoint and never falls back.
- Other Engines query the active Runtime's `/api/engine/capabilities` before a
  write. Capability present selects package apply; capability absent selects
  the existing per-file Legacy Adapter.
- An exact unmatched-route 404 from the capabilities endpoint selects Legacy
  only after the same target answers its health endpoint.
- Capability timeout, 5xx, malformed response, or unhealthy target fails before
  write.
- After the package apply request is sent, timeout, disconnect, untrusted 5xx,
  malformed success, missing digest, or digest mismatch is UNKNOWN. Backend
  does not write metadata and must not replay through the Legacy Adapter.

Raw ZIP upload, folder upload, legacy `/api/skills/upload`, and device-backed
manifest apply all reuse `LocalSkillUploadService`. Platform-managed Teclaw
Whole Artifact remains independent. Manifest planning keeps its installed
digest read so dry-run and zero-write `UNCHANGED` remain accurate.

## Locator compatibility

Backend continues to compute the DB compatibility locator with the existing
provider/layout policy. Create persists `local://<resolved directory>`.
Replace preserves the existing `git_path` byte-for-byte. Engine physical
`target_path` is never persisted. Only the existing Skills Pool cutover and
rollback transaction may bulk-rewrite Local locators.

Engine `action` describes physical storage. The public operation is based on
the locked DB pre-state: absent row means `created`; existing row means
`updated`. These values deliberately need not match when a retry repairs an
orphan package or a missing canonical directory.

If Engine apply succeeds but Backend DB/audit persistence fails, Backend
returns failure, does not delete or roll back Runtime content, and does not run
Runtime Projection. Retrying the same complete package converges.

## Engine ownership

`api/skills` is a thin HTTP adapter over `SkillsService`. The shared
`LocalSkillPackagePublisher` validates, stages, takes a non-blocking
cross-process target lock, exact-replaces the package directory, rolls back on
publication failure before commit, and cleans temporary content. Once the
staged directory has become the authoritative target, cleanup failure for the
old backup does not roll back from a possibly partial backup: the complete new
package remains authoritative and the uniquely named hidden residue is logged
for later cleanup. Engine adapters only select a root and delegate.

`community/core/skills/layout_planner.py` remains the only canonical physical
path table for the standard Python Engines. Package modules contain no second
set of Engine roots. DeepSeek Harness injects its configured Legacy local root
and rejects `POOL` before write. AICoding Runtime remains owned by Aix Relay.

## Package safety

Both Backend and Engine enforce at least:

- compressed ZIP: 10 MiB;
- expanded total: 50 MiB;
- one file: 10 MiB;
- file count: 500;
- path length: 256 characters;
- normalized relative paths only;
- regular files/directories only, no symlink or special entry;
- exactly one root `SKILL.md`, with manifest name equal to `skill_name`;
- the Legacy Backend entry point additionally accepts its historical
  case-insensitive manifest filename and YAML-only manifest, canonicalises the
  filename to `SKILL.md`, and sends those canonical bytes to the Engine.

## Validation

- shared publisher: create, exact replace/stale removal, replay, unsafe ZIP,
  limits, lock conflict, rollback and rollback failure;
- Engine adapters: OpenClaw/community Claude Legacy+Pool; OCB corp
  Claude/Hermes Legacy+Pool; DeepSeek Harness Legacy and Pool rejection;
- Backend: capability selection, guarded legacy 404 fallback, pre-write
  failures, post-send UNKNOWN, Teclaw translation, digest equality, locator
  compatibility, raw/folder/legacy/manifest entry points;
- architecture: package modules cannot duplicate canonical layout roots;
- OCB: exact Avernet gitlink plus corporate transport and Engine adapter tests.

Deployment and live Teclaw/Aix verification are separate release gates and
cannot be inferred from local tests or green PR CI.
