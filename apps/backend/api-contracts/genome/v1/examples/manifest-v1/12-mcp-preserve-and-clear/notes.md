# examples.zh-CN.md §8 (`mcp`), second block

An omitted `config` preserves the saved Bot override; an explicit `{}` clears
it. As with the other cases, this is the first revision of an initially empty
Bot, so both operations leave no override and capture omits `config` for both
MCPs. A Genome entry without `config` still clears a later override on restore.

The compiler equivalence test applies the Manifest, captures the actual result,
then changes the Bot and restores this revision to compare the resulting state.
