# examples.zh-CN.md §9 (`script`)

The body is stored by digest. It uses `$HOME`, a shell variable, not a
`${BOT_*}` placeholder, so it is stored exactly as written.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
