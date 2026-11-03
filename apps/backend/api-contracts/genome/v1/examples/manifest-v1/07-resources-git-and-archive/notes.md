# examples.zh-CN.md §5, second block

`data/archive/` comes from an OSS zip with one wrapper directory
(`kb/`). Recording unpacks it and applies `strip_components: 1` before
encoding, so the tree holds `index.md` and `products/a.md`; the zip itself is not
hashed into the revision.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
