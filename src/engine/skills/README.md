# Engine-side skills shipped as Skill Center content

Skills in this directory are **authored here and uploaded to the Skill Center**;
they are not read off disk by the engine at runtime. Each folder is one complete
Skill package — a single `SKILL.md` is the whole package when the skill needs no
extra files, which is the case for everything here.

## `cli-tools-*` — how the agent finds a bot's CLI tools

`cli_tools` (manifest W9) installs a command as an executable file in a
directory the **engine** owns, and v1 deliberately does **not** put that
directory on the agent's `PATH` (backend spec `2026-09-03-manifest-cli-tools`
D-10, engine spec `2026-09-06-arca-cli-tool-endpoints`). The recorded v1 answer
to "how does the model know the tool is there" is this skill, carried in each
engine's default skillset, and the agent invokes the tool by absolute path.

One skill per engine, because the directory is per engine — it is a literal at
the `LocalCliToolsService` binding site, not something derived at runtime:

| Engine | `engine_type` for the skillset | Directory | Constant |
| --- | --- | --- | --- |
| openclaw | `openclaw` | `/home/admin/.openclaw/cli` | `engines/openclaw/engine.py` `OPENCLAW_CLI_DIR` |
| claude_code | `aicoding` | `/home/admin/.aicoding/cli` | `engines/claude_code/engine.py` `CLAUDE_CODE_CLI_DIR` |

**If an engine's constant moves, the matching `SKILL.md` moves with it.** The
path is spelled out in the skill body *and* in its `description` (so the model
sees it during skill selection, before the body is read), so a grep for the old
path must come back empty.

A third ARCA engine adds its own constant and its own copy here; nothing in
`core/` learns about either.

### Installing

These are ordinary Skill Center skills — uploading them and adding them to a
default skillset is an operator step, not something this repository automates:

1. Upload the folder as a Skill package (`SKILL.md` at the package root).
2. Add it to the matching engine's default skillset (`默认技能集-openclaw`,
   `默认技能集-aicoding`), which `ensure_default_skill_set` creates empty per
   `engine_type`.

Nothing in the backend or the engine references these skills by name, so
membership in the default skillset is the only thing that puts one in front of a
model.

### Known costs, carried over from D-10

`mytool --help` does not work without the absolute path; every invocation
depends on the skill actually being read; and a tool that shells out to a
sibling tool by bare name does not find it. The skill teaches a per-command
`PATH=` prefix as the workaround. `PATH` injection is the real fix, it is
engine-side only, and it touches no schema, API, table or artifact field —
`scripts/modules/bots.sh` already does exactly this for `bcs-cli` on singlebox.
