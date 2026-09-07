# Claude Code local Skill activation repair

## Problem and scope

On community Claude Code, a successfully uploaded local Skill remains absent from
`.claude/skills`: the bulk symlink adapter drops the request payload and the port
calls unsupported Relay methods. The error envelope becomes a successful empty
result. Separately, mapping verify/publish assumes the active directory exists.

Repair Engine activation only. Keep Backend mapping policy, cwd, Bot creation,
Docker startup, GitHub workflows and Corp-specific path conversion unchanged.

## Required behavior

- HTTP bindpath forwards source/target mappings and `clean_target_dir` to local
  filesystem operations; no Relay is required for activation or cleanup.
- First activation creates the target directory and readable Skill symlink.
  Retry is idempotent; replacement, cleanup and errors report actual outcomes.
- Validate paths, source availability and target conflicts before mutating links.
  Target parent chains must not traverse existing symlinks; check this before
  resolving paths, including links created by previous requests. Do not
  overwrite ordinary files/directories or delete uploaded sources. Cleanup
  directories must also be real directories with no symlink ancestors.
- Forward relative sync and cleanup payloads across the same private port seam.
  Retain the existing public HTTP DTOs and response fields. Full reconciliation
  cleans active links outside real Skill packages; links inside directories
  containing `SKILL.md` are package content and must be preserved, including
  packages not selected in the request and empty-set cleanup.
- Missing active directories are empty inventories for mapping verification.
  Verification stays read-only; publishing initializes directories after validation,
  in STRICT and BEST_EFFORT modes. Preserve existing external-entry conflict rules.
- Tests exercise real HTTP -> adapter -> local filesystem, rather than only DTO
  construction from a fake successful Relay response.

## Compatibility and proof boundary

Avernet baseline: `15484b260fc7f7a655ddbc8070b152b70a3b15dc` (github/dev).
OCB inspected ref: `642358c13e3ca387b9eca227afdc9e5023c039a6` (origin/dev), with
`ocb-public` gitlink `8aaec59ee33d93d54c2b08e379fa9ae18e4d696d`.
Corp assembles its own ClaudeCodeSkillsService, not the changed community port.
It shares mapping filesystem helpers, whose existing regression suite must pass.
An Avernet PR does not itself update the OCB gitlink or deploy either environment.

The existing Backend empty-set cleanup hardcodes an OpenClaw directory; that is
an independent caller defect and is not silently remapped by this Engine repair.
Model auto-invocation policy (`disable-model-invocation`) is unchanged.
