# Bot Config Manifest v1 ↔ Genome Revision

How every field of a Bot Config Manifest v1 document
(`docs/bot-config-manifest/manifest-schema.zh-CN.md`,
`core/bot_config_manifest/schema/`) is handled when a revision is recorded
from the document, and what compiling the revision back produces. Recording
rules: [README §7](README.md#7-recording-a-revision-from-a-manifest-document).
Worked cases: [`examples/manifest-v1/`](examples/manifest-v1/).

## Round-trip

Record a Manifest document **M** as a revision, then compile the revision to a
Manifest document **M′**. The round-trip holds when **applying M′ gives the
same bot that applying M gave when the revision was recorded**. M and M′ are
not textually equal: M′ points at the content store by digest where M named
git or OSS sources.

M′ uses two Manifest source kinds that do not exist yet. Their spelling below
is **proposed**; RSI-04 fixes it:

```yaml
source: {protocol: content_store, digest: "sha256:<hex64>"}   # bytes from the manifest content store; no fetch
source: {protocol: center, version: "v7"}                     # a pinned Skill Center version
```

Until RSI-04 lands, the round-trip is checked on paper: every case's M is
accepted by the real Manifest validator (or refused, where stated), its
revision validates against the schema with real digests and id, and M′ is
derived from the revision field by field as below.

## Outcomes

| Outcome | Meaning |
| --- | --- |
| **maps** | The value has a field in the revision and comes back in M′ |
| **normalized** | Maps, after a rewrite that does not change behaviour |
| **resolved** | Used at record time to fetch or compute bytes; the revision keeps only the result (a digest) |
| **provenance** | Not needed after recording; kept only in the stored copy of M |
| **ignored** | Not part of genome v1; M′ omits it, so apply leaves the bot's state as it is |
| **refused** | Recording fails. Usually `PUT /config-manifest` refuses it too; rows marked *recording only* are accepted by the Manifest but cannot be recorded |

## Top level

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `schema_version` | provenance | M′ always says `1` |
| `sources` (named sources) | resolved | Each entry using one is fetched; M′ has no `sources` |
| `manifest` | maps | Its categories, below |
| `script` | maps | `spec.script`, below |
| an absent category | resolved | Filled from the parent revision, or the bot's current state for a first revision (README §7); M′ always lists every category |
| `apply_once` (reserved) | refused | Refused by the Manifest validator |

## Sources and per-entry fetch fields

| Manifest field | Outcome | Notes |
| --- | --- | --- |
| git `protocol`, `url`, `ref`, `subpath`, `mode` | resolved | The commit is fetched once; source addresses are not hashed, so the same bytes from a moved repository give the same revision |
| oss `protocol`, `bucket`, `key` | resolved | |
| `auth` (credential name) | provenance | Never a secret value; not needed after the fetch |
| `from` (named-source reference) | resolved | |
| entry `subpath` | resolved | Selects files inside the fetched tree or archive |
| entry `digest` (OSS pin) | resolved | Checked against the fetched archive. The revision's digest is of the unpacked files, not the archive |
| `on_fetch_failure` | provenance | Only matters while fetching |
| `unpack`, `strip_components` | resolved | Applied before the tree is encoded, so a digest never depends on archive compression |
| `${BOT_ENGINE_TYPE}`, `${BOT_ENV}`, `${BOT_TENANT}`, `${BOT_ARCH}` | resolved | Substituted once with the recording bot's values (README §7) |
| inline `content` | resolved | Stored in the content store; the entry gets its digest |

## `manifest.identity`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `type` | maps | `spec.persona[].type` (the key). `MEMORY.md` and `IDENTITY.md` are refused by the Manifest already; the engine-specific set (`claude_code`: `CLAUDE.md` only) is checked at record time |
| the file's bytes | resolved | `spec.persona[].digest`; M′: `source: {protocol: content_store, …}`. Must be valid UTF-8; apply refuses anything else, so recording does too |
| the AGENTS.md reference block | normalized | Removed when capturing a bot's current state, then the file becomes `content.rstrip() + "\n"`; apply re-adds the block, so only trailing whitespace at the end of AGENTS.md can change (README §7) |
| `identity: []` | maps | `persona: []`; M′ `identity: []` |

## `manifest.skills`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `name` | maps | `spec.skills[].name` (the key) |
| a `name` outside `^[A-Za-z0-9-]+$` (dots, underscores) | refused (*recording only*) | The Manifest accepts it, but apply requires the package metadata name to match that rule and equal the entry name, so it can never be applied |
| a skill whose files are not a valid package | refused (*recording only*) | Missing or invalid `SKILL.md` front matter, a metadata name different from the entry name, or over the package limits (README §4.2); apply would refuse it too |
| the skill's files (git directory or unpacked OSS zip) | resolved | `origin: local`, `digest` of the `acm-tree-v1` tree of the files `SkillPackageValidator` keeps (wrapper directory stripped, ignored paths dropped; README §4.2); M′: `source: {protocol: content_store, …}`, from which apply builds the skill package zip |
| `unpack` | resolved | |
| a Skill Center skill | maps | `origin: center`, `center_version`. Manifest v1 cannot declare one; it comes from capturing a bot's current state. M′: `source: {protocol: center, …}` (RSI-04) |

## `manifest.resources`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `path` | normalized | `spec.resources[].path` (the key). Empty and `.` segments are removed (`./faq.csv` → `faq.csv`); a trailing `/` (directory) is kept |
| a path with no segment left after normalization (`./`, `.`) | refused (*recording only*) | It would name the workspace root, which no resource key can |
| a written path over 1024 bytes (the resource path, plus the member path inside a directory) | refused (*recording only*) | Leaves room for the workspace root under a file system's 4096-byte path limit (README §4.2) |
| a file (or directory member) the workspace does not admit | refused (*recording only*) | An extension outside `ALLOWED_EXTENSIONS` (for example `.sh`, `.toml`, none) or over 500 MB: apply's `admission_refusal` would refuse it |
| a file path equal to a directory path without its `/` | refused (*recording only*) | e.g. `data/guide.md` with `data/guide.md/`: the Manifest's duplicate check compares the raw strings, but apply writes both to `data/guide.md`, and both would have the gene path `resources/data/guide.md` |
| a path beneath a file entry | refused (*recording only*) | e.g. `data/guide.md` with `data/guide.md/notes.csv`: the Manifest checks nesting only beneath directory entries, but apply writes the file and then cannot write beneath it |
| paths that collide or nest only after normalization | refused (*recording only*) | e.g. `faq.csv` with `./faq.csv`, or `data/` with `./data/a.md`: the Manifest's duplicate and nesting checks compare the raw strings |
| a file entry's bytes | resolved | `digest` of the bytes |
| a directory entry's files | resolved | `digest` of the `acm-tree-v1` tree; M′: content-store source |
| `unpack`, `strip_components` | resolved | Applied before encoding |
| a resource removed from the document | maps | Absent from the revision. Apply does not remove it from the bot today ([#2162](https://github.com/inclusionAI/Avernet/issues/2162)) |

## `manifest.mcp`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `server_code` | maps | `spec.tools.mcp[].server_code` (the key) |
| `config` absent | maps | No `config`: no bot override |
| `config.url` | normalized | Scheme lower-cased |
| `config.url` with userinfo (`https://user:token@host/…`) | refused (*recording only*) | The revision is never deleted, so a credential in the URL would be stored for good; the Manifest refuses userinfo in source URLs but not here |
| `config.headers` | maps | Names and values as written ([#2860](https://github.com/inclusionAI/Avernet/issues/2860)). Names are not case-folded: the stored spelling is what reaches the device, and not every apply-time merge compares names case-insensitively |
| `config.endpoint_env` | maps | |
| `config.transport_protocol` | normalized | Upper-cased (the Manifest validator already does this) |
| a server the recording actor may not enable, or a `config` that is not a valid bot override | refused | Apply's `McpMaterialiser.resolve` refuses it (`_permitted`, `validate_bot_override`); recording runs the same checks with the recording bot's context (README §6.4) |
| user-level MCP config, Center defaults | not in the revision | Read at apply time ([#2859](https://github.com/inclusionAI/Avernet/issues/2859)) |

## `manifest.cli_tools`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| every field (`name`, `version`, source, `digest`, `subpath`, `unpack`) | ignored | Not in the revision; M′ has no `cli_tools`, so apply leaves the bot's CLI tools untouched. acli replaces them before the revision feature goes live |

## `manifest.engine_config`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `engine_config` (any content) | refused | Refused by the Manifest on every bot today; `spec.engine_config` is always `{}` until [#2858](https://github.com/inclusionAI/Avernet/issues/2858). M′ has no `engine_config` |

## `script`

| Manifest field | Outcome | Revision / M′ |
| --- | --- | --- |
| `body` | resolved | Placeholders substituted as apply would, then `spec.script.digest`; M′: `script: {body: …}` with the stored body |
| `script` absent | resolved | Filled like any omitted category: from the parent revision, or the bot's current state for a first revision |
| `script: null` | maps | `spec.script: null` (no script); M′ `script: null`. Apply reads `null` as "absent" today, so M′ cannot remove an existing script ([#2862](https://github.com/inclusionAI/Avernet/issues/2862)) |

## Revision fields with no Manifest counterpart

| Revision field | In M′ |
| --- | --- |
| `spec.memory` | Omitted while `null`. A `memory` category is added to the Manifest by RSI-04 with the engine memory projection (RSI-05) |
| `policy` | Omitted: the policy governs evolution, not apply |
| `revision` metadata | Omitted. Promotion records which revision compiled to the applied document's digest (01-genome.md §5.5) |

## Cases

| Case | `examples.zh-CN.md` | Outcome |
| --- | --- | --- |
| [`01-complete`](examples/manifest-v1/01-complete/) | §1.2 as published | refused (`engine_config`) |
| [`02-complete-without-engine-config`](examples/manifest-v1/02-complete-without-engine-config/) | §1.2 without `engine_config` | round-trips; `cli_tools` ignored |
| [`03-sources-only`](examples/manifest-v1/03-sources-only/) | §2 | round-trips (empty bot) |
| [`04-engine-config`](examples/manifest-v1/04-engine-config/) | §3 | refused |
| [`05-identity`](examples/manifest-v1/05-identity/) | §4 | round-trips |
| [`06-resources-file-and-directory`](examples/manifest-v1/06-resources-file-and-directory/) | §5, first block | round-trips |
| [`07-resources-git-and-archive`](examples/manifest-v1/07-resources-git-and-archive/) | §5, second block | round-trips |
| [`08-skills`](examples/manifest-v1/08-skills/) | §6 | round-trips |
| [`09-cli-tools`](examples/manifest-v1/09-cli-tools/) | §7 | ignored (empty bot) |
| [`10-mcp`](examples/manifest-v1/10-mcp/) | §8 | round-trips |
| [`11-script`](examples/manifest-v1/11-script/) | §9 | round-trips |

Each case directory holds `manifest.yaml` (M), `notes.md`, and either
`revision.json` and `compiled.yaml` (M′) or `expected.json` with the refusal.
The fetched bytes are the fixtures in
[`examples/manifest-v1/sources/`](examples/manifest-v1/sources/).
