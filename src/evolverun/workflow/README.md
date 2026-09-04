# Task Guard / Workflow Workspace

This directory owns the V5 Task Guard surface rooted at `/workflows/workspace`.

- Source: ClawWeb `origin/master@46b072308c35b8e6856145b56653a20550465d6c`
- Method: copy first; original repository-relative paths are preserved.
- Runtime: OCB overlays this directory onto the assembled ClawWeb build context.
- Scope: Workspace UI plus the workflow/run/health/analysis/callback closure it uses.
- Constraint: no business redesign, API rename, schema rename, or new module framework.

ClawWeb may retain copies for legacy modules, but V5 mounts the Task Guard implementation
from this Avernet directory.
