# examples.zh-CN.md §6 (`skills`)

Both forms become `origin: local` skills stored as `acm-tree-v1` trees: the
git directory, and the OSS zip after unpacking. The Manifest's `digest` pins the
zip it fetches; the revision's digest is of the files the Skill Center validator keeps
after unpacking: the zip's `order-lookup/` wrapper directory is stripped, and the macOS
files in both sources (`.DS_Store`, `__MACOSX/`, `._SKILL.md`) are dropped.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
