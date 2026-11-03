# examples.zh-CN.md §1.2 without `engine_config`

Real digests in place of the doc's shortened ones, as in `01-complete`.

- `sources`, `from`, `subpath`, `auth`, `on_fetch_failure`, `unpack`: used to fetch, then dropped;
  the provenance copy of this YAML keeps them.
- `SAFETY.md` was inline `content`; it is stored and referenced by digest like the fetched files.
- `data/kb/` and both skills are `acm-tree-v1` trees. `order-lookup` was an OSS zip: it is unpacked
  first, so its digest does not depend on how the zip was compressed.
- A skill's tree is the files the Skill Center validator keeps: the zip's `order-lookup/` wrapper is
  stripped, and the macOS files in both skills (`.DS_Store`, `__MACOSX/`, `._SKILL.md`) are dropped.
- `cli_tools` is ignored: not in the revision and undeclared in the compiled state, so apply
  leaves the bot's CLI tools as they are.
- Equivalent: the revision compiles to the same desired state — identity files, skills, resources,
  MCP server and script — that `manifest.yaml` compiles to.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
