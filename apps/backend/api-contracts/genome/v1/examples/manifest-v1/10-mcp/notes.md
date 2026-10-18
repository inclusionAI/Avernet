# examples.zh-CN.md §8 (`mcp`)

The MCP entry has no bytes, so its `config` is stored inline. The header name is
lower-cased (`X-Project` → `x-project`); apply compares header names
case-insensitively, so behaviour is unchanged. Only the bot-level override is
pinned: user-level MCP config is read at apply time
([#2859](https://github.com/inclusionAI/Avernet/issues/2859)).

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
`compiled.yaml` uses the content-store and Center sources that RSI-04 adds to
the Manifest; their spelling is **proposed** and fixed by RSI-04.
