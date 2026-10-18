# examples.zh-CN.md §9 (`script`)

The body is stored by digest. It uses `$HOME`, a shell variable, not a
`${BOT_*}` placeholder, so it is stored exactly as written.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
`compiled.yaml` uses the content-store and Center sources that RSI-04 adds to
the Manifest; their spelling is **proposed** and fixed by RSI-04.
