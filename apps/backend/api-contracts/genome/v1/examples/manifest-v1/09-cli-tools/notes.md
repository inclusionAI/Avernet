# examples.zh-CN.md §7 (`cli_tools`)

Ignored: CLI tools are not part of genome v1 (acli replaces them). The
revision is the empty bot and its compiled state leaves `cli_tools` undeclared, so
apply leaves the bot's CLI tools untouched.

Every case records the bot's **first** revision on a bot that has nothing yet,
so categories the document leaves out are filled with "none" (README §7). The
fetched bytes are the fixtures under [`../sources/`](../sources/).
The revision compiles into the apply engine's typed desired state (`compile_spec`,
`core/bot_genome/compile.py`); the contract tests check that it equals the state
the Manifest compiler produces from `manifest.yaml` over these fixtures.
