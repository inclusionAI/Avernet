# examples.zh-CN.md §4 (`identity`)

`SOUL.md` and `RULES.md` are fetched from git; `SAFETY.md` is inline and stored
by digest. Persona entries are sorted by `type`.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
