# CLAUDE.md

Claude Code should follow the repository-wide instructions in `AGENTS.md`.

In particular, do not introduce Python `T | None` types unless `None` is an
intentional state in the contract; required values must remain non-optional.

Keep this file intentionally small so agent instructions do not drift. Update
`AGENTS.md` when project-wide rules change.

Before pushing, follow the pre-push target contract in `AGENTS.md`. The default
target is `origin/dev`; set `avernet.prePush.mergeTarget` for a persistent
override or `AVERNET_PRE_PUSH_MERGE_TARGET` for one `git push`. The hook must
fetch that target and use its merge base rather than a direct target-to-head
diff.
