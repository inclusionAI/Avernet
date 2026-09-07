# Claude Code local file I/O and workspace compatibility

Status: implementation of Plan B, targeting dev. The relay file-RPC proposal
(#1955) remains a backup and is not implemented by this change.

## Scope and contract

The community Claude Code runtime shares its filesystem with the Python Engine.
The existing HTTP FileService -> ClaudeCodeFileAdapter -> ClaudeCodeFilePort
boundary remains. The concrete file port performs local I/O; chat and other
relay capabilities retain their transport. No Node file RPC, Base64 file
transport, extra relay credential, or automatic transport fallback is added.

File operations cover the existing workspace, identity, configuration, and
Local Skill consumers. Paths must be engine-view addresses within configured
engine roots. Community BaaS addressing translates only this Bot's known host
root; it does not import enterprise path rules. The existing workspace logical
namespace (`workspace/<relative>`) resolves in Engine to the retained configured
cwd, so Backend need not infer an old Bot's filesystem location. Absolute saved
Local Skill locators are never silently rewritten. Corp and singlebox retain
their existing addressing assembly.

Local upload source directories follow the current engine Legacy/Pool layout.
Content storage is distinct from active discovery; creation returns inactive.
Existing Pool state remains authoritative, without a migration side effect.
Recursive listing and binary readback must preserve complete packages. Reuse
existing staged replacement, verified backup and restoration; do not substitute
a new overwrite algorithm. Filesystem errors propagate distinctly. Deleting a
symlink removes the link, not its target; root deletion and path escape fail.

## New and existing Bot workspaces

A newly persisted Claude Code Bot receives a default cwd in its existing ext
metadata, defaulting to `/home/admin/.claude_code/workspace` and preserving an
explicit valid value. Idempotent creation of an existing Bot does not backfill
or rewrite this field. ACK start commands carry this recorded initial default.
No schema migration is required; managed deployment does not consume this flag.

Startup validates saved Engine/relay cwd fields, explicit configuration and
running process configuration before overwriting either environment file. A
creation default is used only when no saved configuration exists. Unknown or
conflicting old state is an error, never evidence of a fresh Bot. Existing
processes are not restarted on such an error. Existing session bindings and
project files are not moved; the relay's existing resume semantics remain.

Startup runs after the home volume is mounted and creates the resolved cwd
without recursively changing ownership of historical files. SDK configuration
and history are not conflated with the workspace. This is not a Pool migration.

## Validation

- Real HTTP router, Claude Code adapter and concrete file port over temporary
  directories: no mocked relay/file implementation for the core file proof.
- Backend Local Skill create/replace and real package storage over Engine HTTP,
  followed by real mapping publish/verify; nested and binary content, explicit
  activation, and injected metadata-switch failure restoring the complete old
  package. Repositories/permissions and device transport may be test doubles.
- Existing workspace/identity/config consumers and profile-dependent addressing.
- Startup CLI tests: explicit new default, retained old cwd, missing/conflicting
  configuration, spaces, invalid inputs and running-vs-saved disagreement.
- OpenClaw regression and OCB contract/assembly impact review. A passing public
  suite does not prove a particular enterprise image has integrated the change.
- Existing Skill size limits remain: compressed 10 MiB, individual file 10 MiB,
  expanded 50 MiB, 500 entries. Do not impose these on unrelated generic files.

## Not included

Independent Engine/relay filesystems, automatic history/locator migrations,
clearing old directories, implicit activation, new public file-management
features, a cross-engine filesystem framework, or deployment/merge authorization.
Report local validation, CI, review, merge, deployment and live validation
separately. This PR supplies source changes and tests, not cloud rollout proof.
