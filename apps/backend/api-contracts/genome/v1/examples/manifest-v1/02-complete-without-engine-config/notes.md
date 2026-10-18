# examples.zh-CN.md §1.2 without `engine_config`

Real digests in place of the doc's shortened ones, as in `01-complete`.

- `sources`, `from`, `subpath`, `auth`, `on_fetch_failure`, `unpack`: used to fetch, then dropped;
  the provenance copy of this YAML keeps them.
- `SAFETY.md` was inline `content`; it is stored and referenced by digest like the fetched files.
- `data/kb/` and both skills are `acm-tree-v1` trees. `order-lookup` was an OSS zip: it is unpacked
  first, so its digest does not depend on how the zip was compressed.
- `cli_tools` is ignored: not in the revision, omitted from `compiled.yaml`, so apply leaves the
  bot's CLI tools as they are.
- Equivalent: applying `compiled.yaml` gives the same identity files, skills, resources, MCP server
  and script that applying `manifest.yaml` did when it was recorded.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
`compiled.yaml` uses the content-store and Center sources that RSI-04 adds to
the Manifest; their spelling is **proposed** and fixed by RSI-04.
