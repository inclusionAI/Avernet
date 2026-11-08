# examples.zh-CN.md §1.2

The published document, with real digests in place of the doc's shortened
ones (`sha256:3e7a…`, `sha256:9f2c…`), which the validator would also refuse.

Refused, by the Manifest validator and therefore by recording: it declares
`engine_config`, which nothing applies yet ([#2858](https://github.com/inclusionAI/Avernet/issues/2858)).
`02-complete-without-engine-config` is the same document without that section.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
