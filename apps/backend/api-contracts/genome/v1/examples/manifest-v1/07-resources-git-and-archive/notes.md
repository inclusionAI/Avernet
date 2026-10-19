# examples.zh-CN.md §5, second block

`data/archive/` comes from an OSS zip with one wrapper directory
(`kb/`). Recording unpacks it and applies `strip_components: 1` before
encoding, so the tree holds `index.md` and `products/a.md`; the zip itself is not
hashed into the revision.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
`compiled.yaml` uses the content-store and Center sources that RSI-04 adds to
the Manifest; their spelling is **proposed** and fixed by RSI-04.
