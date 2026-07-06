# AGENTS.md

This file provides Codex-specific guidance for work under `src/bcs`.

## Read Local Guidance First

Before changing BCS code, read `src/bcs/CLAUDE.md` and follow its module
architecture, layering, testing, and coding rules. If this file and
`CLAUDE.md` overlap, treat `CLAUDE.md` as the detailed local source of truth.

## No Global Formatting

Do not run `cargo fmt`, `cargo fmt --all`, or any global formatter in BCS.
Keep whitespace and style edits limited to the lines that must change for the
task. Avoid import reordering, line wrapping, or formatting churn in unrelated
code.

If formatting is accidentally applied beyond the intended files, stop and clean
the formatter-only diff before continuing.
