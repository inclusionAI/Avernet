# examples.zh-CN.md §8 (`mcp`)

The MCP entry has no bytes, so its `config` is stored inline, with header names
as written (`X-Project`). Only the bot-level override is
pinned: user-level MCP config is read at apply time
([#2859](https://github.com/inclusionAI/Avernet/issues/2859)).

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
