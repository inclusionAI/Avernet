# `adapters.bot_genome`

Adapters between the production Bot Genome registry and the RSI runtime.

## Current implementation

- `real_bot_genome.py` captures and resolves real Bot Genome data through the
  platform-facing APIs.
- [`plugins/evolution/genome_registry.py`](../../plugins/evolution/genome_registry.py) materializes persona files,
  local Skill trees, and file resources for a strategy workspace.
- Local Skill content is stored as `acm-tree-v1` and must be expanded into all
  member files; it is not equivalent to reading the tree digest as `SKILL.md`.

The adapter has been exercised by the limited real-bot 01–06 smoke path, but
candidate persistence, drift comparison, and live promotion/apply are not
claimed as complete end-to-end behavior.
