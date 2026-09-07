# Claude Code Local Skill file I/O and storage addresses

Status: narrowed Plan B, targeting dev in PR #1961. The Node relay file-RPC
proposal (#1955) remains an unimplemented backup.

## Problem and scope

The community Claude Code file port calls Node relay file RPCs that do not exist.
The Backend also derives remote Claude Local Skill uploads from the shared
OpenClaw repository root. These are distinct defects: implementing local I/O
alone can write a package which the existing Claude mapping rejects as outside
its managed source layout.

Repair the community Claude Code file port and only the Local Skill storage
address selection needed for upload, replacement, read and cleanup. Keep HTTP
FileService -> ClaudeCodeFileAdapter -> ClaudeCodeFilePort. The concrete port
uses the shared Engine/runtime filesystem; chat continues over the relay.
Preserve bytes, recursive listing, standard filesystem errors, safe symlink
removal, and configured-root containment. No Base64 transport is introduced.

## Backend interface

Reuse SkillServiceFactory's package storage and reopening methods. A small
LocalSkillStorageResolver Plugin API returns a Legacy package root, not a
profile flag. Public Skill services and WorkspacePathFactory do not distinguish
corp/community. The default provider preserves existing configured roots;
community's provider selects the existing Claude Code layout contract. Shared
repository synchronization and other engines' configured roots are unchanged.

Use the same resolved root when constructing request-scoped SkillService,
creating staged/canonical/backup storage and validating existing locators. The
existing factory returns the persistent locator and a device storage port;
keep their existing address adapters rather than adding a duplicate address
DTO or a general filesystem framework. Existing Pool resolution takes priority
and alone controls runtime_uses_pool_paths. Do not masquerade Legacy paths as
Pool state. New absolute Local Skill locators feed the existing mapping logic;
no profile-specific symlink generation or new mapping protocol is added.

Historical locators are never rewritten or moved. Existing readable addresses
retain their identity through file I/O. Package replacement and cleanup retain
the current canonical-root containment requirement; incompatible historical
locators must fail before any package writes or metadata changes. Supporting a
migration or expanding roots to bypass this check is outside this change.

## Validation

- Real HTTP router, adapter and local file implementation: binary bytes,
  recursive listings, missing/empty directories, overwrite and removal errors,
  traversal/root protection and symlink unlink behavior.
- Factory conformance with configured and community providers: persistent
  locator equals the address reopened for the same package; Pool precedence;
  other engines unchanged; historical incompatible locator rejected before I/O.
- Real package storage through Engine HTTP: create, same-name replacement,
  nested/binary contents and metadata-failure rollback. Use the actual storage
  factory and local-address provider; repositories/device transport may be doubles.
- Existing mapping publish/verify against the uploaded source, with its active
  root pre-created. This proves mapping compatibility, not fresh-container
  bootstrap or API-level activation. Upload stays inactive until activated.
- Narrow DI, architecture, type and contract checks; Standards/Spec review;
  affected full module suites and existing GitHub CI. No workflow changes.
- Matching OCB source/gitlink impact analysis and corp file regression. Public
  CI does not prove enterprise rollout or deployed-image compatibility.

Existing package limits stay unchanged: compressed and per-file 10 MiB,
expanded 50 MiB, 500 entries. Generic files do not gain these Skill-specific caps.

## Explicitly excluded

Bot creation metadata, ACK command arguments, Dockerfile/startup scripts, cwd
selection or conflict checks, automatic history migration, fresh activation
root initialization, general workspace/identity/config address redesign,
shared-repository delivery, Pool migration, merging and cloud deployment.
