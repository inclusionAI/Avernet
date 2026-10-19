# examples.zh-CN.md §5, first block

A file entry is the file's bytes; the directory entry `data/kb/` is the
`acm-tree-v1` encoding of everything under `kb/` (including the subdirectory
`returns/`). `on_fetch_failure` only matters while fetching and is dropped.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
`compiled.yaml` uses the content-store and Center sources that RSI-04 adds to
the Manifest; their spelling is **proposed** and fixed by RSI-04.
