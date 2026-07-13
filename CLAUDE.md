# CLAUDE.md

Claude Code should follow the repository-wide instructions in `AGENTS.md`.

In particular, do not introduce Python `T | None` types unless `None` is an
intentional state in the contract; required values must remain non-optional.

Keep this file intentionally small so agent instructions do not drift. Update
`AGENTS.md` when project-wide rules change.
