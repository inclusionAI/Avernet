# Bot Genome contract, version 1

This directory is the normative contract for the Bot Genome: the format of a
**Genome Revision**, the format of a **Genome Patch**, the **gene path**
grammar both use, the default **risk tier** of every patch op, and the rules
for computing a revision's identity. Design and rationale:
[`docs/bot-evolution/01-genome.md`](../../../docs/bot-evolution/01-genome.md);
decision: [ADR 0019](../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md).

| File | What it defines |
| --- | --- |
| [`genome-revision.schema.json`](genome-revision.schema.json) | A Genome Revision: `spec`, `policy`, and platform-written `revision` metadata |
| [`genome-patch.schema.json`](genome-patch.schema.json) | A Genome Patch and every op |
| [`risk-tiers.json`](risk-tiers.json) | The default tier of every op, the raises, and the rewrite threshold |
| [`canonical-json-vectors.json`](canonical-json-vectors.json) | Test vectors for canonical JSON and identity |
| [`rewrite-vectors.json`](rewrite-vectors.json) | Test vectors for the rewrite measure (§6.3), including the threshold and cell-limit boundaries |
| [`manifest-v1-compatibility.md`](manifest-v1-compatibility.md) | How every Bot Config Manifest v1 field maps to a revision and back |
| [`examples/`](examples/) | Valid and invalid records with real digests, and Manifest v1 round-trip cases |

The JSON Schemas use draft-07, like
`kernel/bot_config/artifact.schema.json`. A schema checks shape; the rules in
this file that need more than one record (a patch against its base, a policy
against the risk tiers) are checked by whoever records the revision, the
Genome Registry in Backend (work item RSI-03).

**Consumers.** Backend's Genome Registry validates every revision and patch it
records and is the authority: an invalid patch is answered `422`. The
`apps/evolution` service and the strategy SDK read these same files to check a
patch before submitting it; they never import Backend code. The files are
contract data, not an endpoint: there is no runtime schema download.

Contract test: `apps/backend/tests/community/contracts/test_bot_genome_schema.py`.

## 1. Versioning

- Every revision carries `genome_schema: 1`; every patch carries
  `patch_schema: 1`.
- A change that alters the canonical bytes of an existing valid record bumps
  the version: a new required field, a renamed field, a changed sort rule, a
  changed digest formula. Because the revision id hashes those bytes, such a
  change gives existing content a new id, so a version bump comes with a
  migration that re-hashes stored revisions.
- A change that existing records are unaffected by does not bump the
  version: a new op, a new optional field that existing records omit, a newly
  allowed enum value.
- `spec.engine_config` (always `{}` in v1) and `spec.memory` (`null` until
  the engine memory projection lands, RSI-05) are present from v1 so that
  enabling them later does not change any existing revision's bytes.

## 2. Gene paths

A **gene path** names one part of a bot's `spec`. The same grammar is used by
`policy.mutable_genes`, `policy.pins`, `policy.risk_overrides`, a binding's
`allowed_genes`, every patch op's target, and diff output.

```abnf
gene-path = category *( "/" segment )
category  = "persona" / "skills" / "memory" / "resources" / "tools" / "engine_config" / "script"
segment   = 1*( ALPHA / DIGIT / "." / "_" / "-" / pct-encoded )   ; not "." and not ".."
pct-encoded = "%" HEXDIG HEXDIG                                     ; uppercase; only for bytes outside the set above
```

| Gene path | Means |
| --- | --- |
| `persona` | every persona file |
| `persona/SOUL.md` | the persona file `SOUL.md` |
| `skills/refund-policy` | the skill `refund-policy` |
| `skills/refund-policy/SKILL.md` | the file `SKILL.md` inside that skill |
| `memory` | curated memory: its mode and every item |
| `memory/returns-window` | the memory item with key `returns-window` |
| `resources/data/kb` | the directory resource declared as `data/kb/` |
| `resources/data/kb/faq.md` | the file `faq.md` inside that directory resource |
| `resources/faq.csv` | the file resource declared as `faq.csv` |
| `tools/mcp` | every MCP server |
| `tools/mcp/mcp.x.meet` | the MCP server `mcp.x.meet` |
| `script` | the startup script |

- **Dots are part of names.** `/` is the only separator, so `SOUL.md` and
  `mcp.x.meet` are single segments.
- **Encoding.** Persona types, skill names, memory keys and resource entry
  paths only use the characters `A–Z a–z 0–9 . _ -` inside one name, so they
  are written as they are. An MCP `server_code` (only required to be
  non-empty) and a file path inside a skill or a directory resource (any
  characters a skill package accepts, such as `docs/how to.md`) can contain
  anything. Inside a gene path, every UTF-8 byte of a segment outside
  `A–Z a–z 0–9 . _ -` is written
  `%XX` with uppercase hex, and a name that is exactly `.` or `..` is written
  `%2E` or `%2E%2E`. Bytes in the allowed set are never encoded, so every
  name has exactly one spelling: `mcp:x` is `tools/mcp/mcp%3Ax`.
- **Only the canonical spelling is valid.** A gene path is valid only when
  decoding every segment (as UTF-8) and encoding it again by the rule above
  gives the same string. `persona/SAFETY%2Emd`, `skills/%61bc` and `%FF`
  match the schema's pattern but are refused: covering compares strings, so a
  pin written in another spelling would protect nothing.
- **A directory resource's gene path has no trailing slash.** The entry
  declared as `data/kb/` is `resources/data/kb`.
- **Covering.** A gene path `P` *covers* a gene path `X` when `X == P` or `X`
  starts with `P + "/"`. Matching is by whole segments: `skills/pdf` covers
  `skills/pdf/SKILL.md` but not `skills/pdf-tools`.
- **Overlap.** Two paths *overlap* when either covers the other. Pins and
  risk overrides apply to every op whose gene path overlaps them, so an op
  that changes a whole skill is caught by a pin or override on one of its
  files.
- `policy` is not a gene path. No grammar production reaches it, so no patch,
  binding or rule can name it.

## 3. Canonical JSON and identity

### 3.1 Canonical JSON

Every hashed record is serialized with the JSON Canonicalization Scheme
(JCS, [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785)) and encoded as UTF-8:
object members sorted by the UTF-16 code units of their names, no
insignificant whitespace, strings escaped as RFC 8785 §3.2.2.2 prescribes.

Hashed content uses a subset of JSON, so that every implementation produces
the same bytes:

- **No floats.** Numbers are integers in the range
  `-(2^53 - 1) … 2^53 - 1`. A decimal value is written as a string
  (`"0.2"`). RFC 8785 formats numbers as IEEE doubles, which is where
  implementations disagree most; the subset removes the question.
- **No duplicate object member names** (RFC 8785 requires this too). A
  parser that keeps the last duplicate silently would hash a different record
  than the one sent, so records are parsed with duplicates rejected.
- **Lists are in canonical order.** RFC 8785 keeps array order, so two
  revisions holding the same entries in a different order would get
  different ids. Every list in `spec` and `policy` is therefore sorted and
  free of duplicates, as below. A record whose lists are out of order is
  invalid, not silently re-sorted: the bytes a caller hashes must be the bytes
  the Registry hashes.

| List | Sorted by (ascending, code-point order) | Unique by |
| --- | --- | --- |
| `spec.persona` | `type` | `type` |
| `spec.skills` | `name` | `name` |
| `spec.resources` | `path` | `path` |
| `spec.tools.mcp` | `server_code` | `server_code` |
| memory items (the stored list) | `key` | `key` |
| `policy.mutable_genes`, `policy.pins` | the string | the string |
| memory item `tags`, `source` | the string | the string |

Code-point order on these values equals UTF-8 byte order. JSON Schema cannot
express "sorted"; the contract test and the Registry check it.

Test vectors: [`canonical-json-vectors.json`](canonical-json-vectors.json).

### 3.2 Revision id

```text
revision_id = "sha256:" + lowercase_hex( SHA-256( JCS( {"policy": policy, "spec": spec} ) ) )
```

Only `spec` and `policy` are hashed. `genome_schema` and the `revision`
metadata (parent, provenance, status, `seq`, evaluations, annotations) are not, so
identical content has the same id however and whenever it was produced
(01-genome.md §4.4).

### 3.3 Digests

A digest is the address of stored bytes in the manifest content store:
`"sha256:" + lowercase_hex(SHA-256(bytes))`, matching the store's own format
(`DIGEST_RE` in `core/bot_config_manifest/fetch/limits.py`). What the bytes
are depends on the field:

| Field | Bytes hashed |
| --- | --- |
| persona file, file resource, file inside a skill | the file's bytes, exactly |
| skill (`origin: local`), directory resource | the `acm-tree-v1` encoding of its files (§3.4) |
| `memory.items_digest` | `JCS(items)`, the canonical JSON of the item list (§4.4) |
| `script.digest` | the script body after placeholder substitution (§7), UTF-8 encoded |
| a patch (the candidate id) | `JCS(patch)`, the canonical JSON of the whole patch |

### 3.4 `acm-tree-v1`: one byte string for a directory

A directory is stored and hashed as one byte string in the `acm-tree-v1`
format, the format the manifest content store already uses for git-delivered
trees (`canonical_tree_bytes` in
`core/bot_config_manifest/apply/entry_delivery.py`):

```text
"acm-tree-v1\n"  then, for every file:  <path length>:<path><byte length>:<bytes>
```

- Files are sorted by path in code-point order; lengths are decimal ASCII
  byte counts; paths are UTF-8, relative, `/`-separated, with no empty, `.`
  or `..` segment, no control character (NUL included), no backslash and no
  drive letter (the path rule of the patch schema's `skillFilePath`; a NUL
  or control character in a name would be recorded but fail when apply
  writes the file), no segment longer than 255 bytes of UTF-8 (a file
  system's limit on one name; JSON Schema cannot count bytes, so the Registry
  checks it, for resource paths and patch file paths too), and **unique**: a blob that lists one path twice is
  refused, because it would not identify a single tree. No member path may
  be a whole-segment prefix of another (`a.md` with `a.md/b.md`): no file
  system holds both, and apply would write `a.md`, then fail on
  `a.md/b.md` with the tree half written.
- Only files are recorded. Not preserved: empty directories, file modes
  (including the executable bit), timestamps, symlinks. None of these is
  delivered today either: archives are unpacked with uniform `0644` files and
  `0755` directories and link members are refused (`fetch/unpack.py`), and the
  skill package zip marks every file `0644`.
- The encoding has no compression, so a digest never depends on a zip or
  zlib implementation. A skill or resource fetched as an archive is unpacked
  first (`unpack`, `strip_components` applied); the archive's own bytes are
  never hashed into a revision. Apply builds whatever transport format it
  needs (the skill package zip) from the files.
- It can be decoded back into the files (`decode_tree_bytes`).

Cost, accepted for v1 (open decision G-1): a directory is one blob, so a
patch that changes one file of a 150 MiB resource tree stores a new 150 MiB
blob. Per-file blobs with a listing would avoid this and are the recorded
follow-up.

## 4. Revision content

### 4.1 Keys and values

Every entry in `spec` is a **key**, the one field that names it, plus a
**value**:

| Category | Key | Value |
| --- | --- | --- |
| `persona` | `type` | `digest` |
| `skills` | `name` | `origin: local` + `digest` (a tree), or `origin: center` + `center_version` |
| `resources` | `path` (trailing `/` = directory) | `digest` (file bytes, or a tree for a directory) |
| `tools.mcp` | `server_code` | optional `config` |
| memory item | `key` | `text`, `tags`, `source` |

- An entry with stored bytes has a digest. File content is always a digest,
  never inline, whatever way it arrived (fetched, inline in a Manifest,
  written by a patch).
- An entry with no bytes has inline fields only: an MCP server's bot-level
  config, a Center skill's pinned version.
- Keys are stable while values change, so a diff reports "modified", and a
  pin on a key keeps matching after its value changes.

### 4.2 Categories

- **`persona`.** `type` is one of `AGENTS.md`, `BOOTSTRAP.md`, `CLAUDE.md`,
  `GREETING.md`, `HEARTBEAT.md`, `KNOWLEDGE.md`, `OKR.md`, `OUTPUT.md`,
  `README.md`, `RULES.md`, `SAFETY.md`, `SOUL.md`, `TOOLS.md`, `USER.md`:
  `VALID_IDENTITY_FILES` (`core/services/identity.py`) minus the reserved
  `MEMORY.md` and `IDENTITY.md`. A persona file's bytes must be valid UTF-8:
  apply refuses anything else (`IdentityMaterialiser`), and content given by
  digest is not checked on upload, because the upload endpoint also takes
  binary skill and resource files. The set an engine accepts is narrower
  (`claude_code`: `CLAUDE.md` only) and is checked at record time against the
  bot's engine.
- **`skills`.** `name` follows the Skill Center package rule
  `^[A-Za-z0-9-]+$`, and `skills-center`, `skills-local` and `skills-repo`
  are reserved (case-insensitively). The Manifest accepts a wider set (dots,
  underscores), but apply requires the package's metadata name to match this
  rule and to equal the entry name, so such a skill can never be applied.
  `local`: a bot-owned skill whose files are stored. Its tree must be a valid
  Skill Center package, as `SkillPackageValidator` checks at apply: exactly
  one `SKILL.md`, at the root, whose YAML front matter has a `name` equal to
  the entry's name and a non-empty `description`; at most 500 files, 10 MiB
  per file, 50 MiB in total, 256 characters per path. The tree is the
  validator's output, not the files the source served: recording passes the
  fetched files through `SkillPackageValidator` (`validate_directory`, the
  same check the skills materialiser runs) and encodes the
  `ValidatedSkillPackage.files` it returns. That strips a single wrapper
  directory around the package and drops the paths the validator ignores
  (`.DS_Store`, `._*`, `Thumbs.db`, `ehthumbs.db`, `Desktop.ini`, a root
  `__MACOSX/`, any `.git` or `__pycache__` component, `*.pyc`, `*.pyo`), so
  the digest is of what apply delivers. `center`: a governed Skill Center skill pinned by version and
  not copied (ADR 0010); a pinned version is a reference to a published
  version, not evidence that it is active. A Manifest v1 document cannot yet
  declare a Center skill; RSI-04 adds that source.
- **`memory`.** `null`, or `{mode, items_digest}`. `mode` is `seed`, `merge`
  or `replace` (01-genome.md §2.2).
- **`resources`.** `path` is workspace-relative: characters
  `A–Z a–z 0–9 . _ - /`, no leading `/` or `~`, no empty, `.` or `..`
  segment. A trailing `/` makes it a directory entry. With the trailing `/`
  removed, resource paths are unique and none is a whole-segment prefix of
  another, whether or not the shorter one is a directory entry. So these are
  all refused: `foo.md` with `foo.md/` (one workspace path, and one gene path
  `resources/foo.md`), `data/` with `data/a.md` (nesting, which the Manifest
  refuses too), and `foo.md` with `foo.md/b.csv` (a file beneath a file).
  Apply writes the shorter path first and cannot then write beneath it. The
  Manifest checks only nesting beneath a directory entry, so recording
  refuses the other two. More generally, the files the resources write (each
  file resource, and each member of a directory resource under its path) must
  be able to coexist: none may be a whole-segment prefix of another. The Manifest
  accepts empty and `.` segments (`./faq.csv`, `a//b`); recording removes
  them, so each path has one spelling. Every file a resource puts in the
  workspace (a file resource, or each member of a directory resource) must
  pass the workspace admission rule apply uses, `admission_refusal` in
  `core/resources/services/file_service.py`: an allowed extension
  (`ALLOWED_EXTENSIONS`, for example `.md`, `.csv`, `.json`, `.py`, `.png`;
  not `.sh`, `.toml` or no extension) and at most 500 MB. A document whose paths collide or nest
  only after this normalization (`faq.csv` with `./faq.csv`, or `data/` with
  `./data/a.md`) is accepted by `PUT /config-manifest` but refused when
  recorded.
- **`tools.mcp`.** `server_code` names a server in the MCP registry.
  `config`, when present, is the bot-level override with the Manifest's own
  closed set and limits: `url` (absolute `http`/`https` with a non-empty
  host, the Manifest's own parse check, and no userinfo: a `user:token@` in
  the URL would store a credential in a revision that is never deleted, so
  recording refuses it, as the Manifest already does for source URLs; recording lower-cases the scheme,
  which URLs treat case-insensitively), `headers` (names
  1–256 characters, values up to 2000 characters, no CR or LF),
  `endpoint_env` (`PROD`, `PRE`), `transport_protocol` (`SSE`,
  `STREAMABLE_HTTP`). Absent `config` means "no bot override". Header names
  are stored as written and must be unique case-insensitively (the Manifest
  rule). They are not case-folded: the stored spelling is what reaches the
  device, and not every apply-time merge is case-insensitive
  (`mcp/services/config_service.py` merges bot and user headers by
  case-insensitive name, but the composer and device-payload paths use plain
  dictionary updates), so folding could change what is sent. `transport_protocol` is upper-cased, as the Manifest validator
  already does.
- **`engine_config`.** Always `{}` in v1. The Manifest refuses the category on
  every bot because nothing applies it; the genome refuses it too until
  [#2858](https://github.com/inclusionAI/Avernet/issues/2858) defines its keys
  and value types.
- **`script`.** `null`, or `{digest}` of the body. Apply substitutes the
  `${BOT_*}` placeholders in a script body and leaves every other `${…}` (shell
  variables such as `${HOME}`) alone; recording does the same substitution
  once (§7), so the stored body is what apply would write.
- **No `cli_tools`.** CLI tools built through the Manifest are not part of
  genome v1; acli support is added before the revision feature goes live.

### 4.3 Totality

Every category is present in every revision. An empty list (`[]`) or `null`
means "none", never "leave as it is". Compiling a revision emits every
category the Manifest can carry, so applying a revision never depends on what
the bot had before, with the exceptions listed in §7.

### 4.4 Memory items

The list `items_digest` points at is a JSON array of
`{key, text, tags, source}`, sorted by `key`, keys unique, stored as its
canonical JSON. `key` follows `^[a-z0-9][a-z0-9-]*$`; `source` entries are
evidence references `<episode|feedback|eval>:<id>` (01-genome.md §2.2).

## 5. Policy

```jsonc
{"mutable_genes": ["memory", "persona", "resources", "skills"], "pins": ["persona/SAFETY.md"], "risk_overrides": {}}
```

- **`mutable_genes`** is an allowlist: a patch op may change a gene only when
  some entry covers the op's gene path. Everything else is off limits.
- **`pins`** are single items inside mutable genes that stay
  write-protected: an op is rejected when its gene path overlaps a pin. So
  removing a skill that has a pinned file is rejected too.
- **`risk_overrides`** maps a gene path to a tier. An op's tier becomes the
  higher of its default tier and every override whose path overlaps the op's
  gene path. Overlap rather than covering matters: an override on
  `skills/x/SKILL.md` also applies to `skill.update` and `skill.remove` of
  `skills/x`, which change that file too. An override can therefore only raise
  a tier; a lower value has no effect (open decision G-7, closed).
- A pin on a memory item (`memory/<key>`) also blocks `memory.set_mode`,
  whose gene path is `memory`: changing the mode changes how every item,
  including the pinned one, reaches the engine.
- The **default policy** of a new bot is the one shown above: persona, skills,
  memory and resources are mutable, `persona/SAFETY.md` is pinned, and
  `script` and `tools/mcp` are not mutable (open decision G-3, closed).
- No patch can change the policy. An owner changes it by recording a new
  revision (01-genome.md §4.4).

## 6. Patches

A patch is `{patch_schema, base, ops, rationale, evidence}`. `base` is the
revision id the patch was written against and becomes the new revision's
parent. Ops apply in order, each to the result of the previous one; a patch
applies completely or not at all. The new revision's policy is the base's
policy, copied unchanged.

### 6.1 Ops

| Op | Gene path checked | Changes |
| --- | --- | --- |
| `file.edit` | `target` | Edits one existing text file: a persona file, a file in a `local` skill, a file resource, or a file inside a directory resource |
| `persona.add` | `persona/<type>` | Adds a persona file that does not exist yet |
| `persona.remove` | `persona/<type>` | Removes a persona file |
| `skill.add` | `skills/<name>` | Adds a `local` skill; `files` must include `SKILL.md` |
| `skill.update` | `skills/<name>` | Changes files of an existing `local` skill |
| `skill.remove` | `skills/<name>` | Removes a skill (`local` or `center`) |
| `resource.add` | `resources/<path>` | Adds a file resource, or a directory resource from an uploaded tree |
| `resource.remove` | `resources/<path>` | Removes a resource entry |
| `memory.add` | `memory/<key>` | Adds an item whose key is not present |
| `memory.update` | `memory/<key>` | Replaces `text`, `tags`, `source` of a present item |
| `memory.retire` | `memory/<key>` | Removes an item from the current list; it stays in earlier revisions |
| `memory.set_mode` | `memory` | Sets the memory mode |

- No op reaches `tools`, `script`, `engine_config` or the policy. MCP servers
  and the script change only when an owner records a whole Manifest document,
  which keeps tool changes human-only structurally.
- Memory ops are rejected while `spec.memory` is `null`.
- A Center skill's pinned version is changed by an owner recording a
  document, not by a patch.
- Content is given inline (a UTF-8 string, at most 64 KiB once encoded, the
  Manifest's inline limit; the schema's `maxLength` can only bound code points,
  so the byte limit is checked when the patch is recorded) or as
  `{"digest": …}` of bytes uploaded first with
  `PUT …/genome/content`. A directory resource is always added by digest of
  an `acm-tree-v1` blob.

### 6.2 `file.edit` kinds

Edits apply in order to one UTF-8 text file. Lines are separated by `\n`.

- **`replace_section`** `{heading, content}`. `heading` must equal exactly
  one ATX heading line of the file (`#`…`######`, a space, the text), outside
  fenced code blocks. Everything after that line up to the next heading of the
  same or a higher level (or the end of the file) is replaced by `content`;
  the heading line stays.
- **`insert_after`** `{anchor, content}`. `anchor` must equal exactly one
  whole line of the file; `content` is inserted directly below it.
- **`diff`** `{diff}`. A unified diff for this one file: hunks only, starting
  at `@@`, no `---`/`+++` header lines, no fuzz (context and removed lines
  must match exactly), hunks in order and non-overlapping. `\ No newline at
  end of file` is honoured. This kind can express any change to a text file,
  so turning a sandbox's edits into a patch never fails for lack of an op.
- For `replace_section` and `insert_after`, a non-empty `content` that does
  not end with `\n` gets one appended.

`skill.update` changes files with `file_diffs` (`{path, diff}`, same rules as
the `diff` kind, path must exist) and/or `files` (path → inline string,
`{digest}`, or `null` to delete). A path appears at most once across both,
and `SKILL.md` cannot be deleted. Binary files change only through `files`.

### 6.3 Risk tiers

[`risk-tiers.json`](risk-tiers.json) is the normative mapping. For each op:

1. Take the first rule, in file order, whose `op` matches, whose `target`
   covers the op's gene path, and whose `when` (if any) holds.
2. Apply the raises: an op flagged `rewrite` or `guardrail_touch` is at least
   the raise's `min_tier`.
3. Apply `policy.risk_overrides` (§5, by overlap).

A patch's tier is the highest tier over its ops. Annotation changes on a
revision record are not patches (T0). The tier tables in 01-genome.md §6.3
and 08-promotion.md §5.1 are generated from this file and checked by the
contract test.

Conditions used by rules:

- **`description_only`**: the op changes only `SKILL.md`, and before and
  after differ only in the value of the `description` key of its YAML front
  matter (the block between the first two `---` lines); every other key and
  the body are byte-identical.
- **`mode`**: the value `memory.set_mode` sets.

**Rewrite flag.** A text file that exists in the base and that a patch
changes (by `file.edit`, or `skill.update` on a text file) is measured once,
from its content in the base to its content after the whole patch, so
splitting a rewrite over several ops on one file cannot keep each step under
the threshold. Every op that changes the file is flagged `rewrite` when

```text
changed = (len(a) + len(b) - 2 * LCS(a, b)) / (len(a) + len(b))  >  2/5
```

where `a` and `b` are the file's lines in the base and after the patch, and `LCS` is the exact
length of their longest common subsequence of lines (not a heuristic ratio
such as Python's `difflib`). Compute it in integers, with no floating point:
flag when `5 * (len(a) + len(b) - 2 * LCS) > 2 * (len(a) + len(b))`. A file
that is empty before and after is never flagged. The threshold is
`rewrite_threshold` in `risk-tiers.json` (`2/5`, written as a numerator and a
denominator).

**Lines.** A file's bytes are decoded as UTF-8 and split on `\n` only (a `\r`
stays part of its line). A final `\n` ends the last line and does not start a
new, empty one, and an empty file has no lines: `""` is `[]`, `"\n"` is
`[""]`, and `"a"` and `"a\n"` are both `["a"]`.

Exact LCS costs `len(a) * len(b)` in time, which is unbounded for the large
files the limits allow. So the measure is bounded, deterministically, and
every implementation reaches the same answer:

1. Remove the longest common prefix of `a` and `b`, then the longest common
   suffix of what remains (prefix first, so the two never overlap). This
   does not change the result (the removed lines are in every longest common
   subsequence), and it makes a small edit to a large file cheap.
2. If the remaining `len(a) * len(b)` exceeds `rewrite_threshold.lcs_cell_limit`
   (`16777216`, 2^24), flag the file `rewrite` without computing the LCS. A
   change that large is reviewed by a person (T2) rather than scored.
3. Otherwise compute the exact LCS of the remaining lines, with an algorithm
   whose work is bounded by their `len(a) * len(b)` (a dynamic programme, or
   Myers' O((N+M)D) diff falling back to one: `D` can approach `N + M` when
   the two sides are very different in length). `LCS(a, b)` is that plus the
   number of lines removed from each side, and the rule above applies to the
   whole file.

[`rewrite-vectors.json`](rewrite-vectors.json) fixes the answers an
implementation must reach, including a change of exactly 2/5 (not flagged), a
prefix and suffix that would overlap if stripped independently, and the
products exactly at and one row over `lcs_cell_limit`.

**Replacement.** A patch that removes an entry and adds an entry with the
same gene path (§2: a persona type, a skill name, or a resource path with
its trailing `/` removed, so `foo.md` and `foo.md/` are the same entry)
replaces it, and the add is judged as a change to the existing entry, not as
a new one, so a remove-then-add cannot avoid the rewrite flag a `file.edit`
would get. The add is flagged `rewrite` when the old and new entries are
both files whose bytes are UTF-8 text and the line rule above flags them
(`a` the old file, `b` the new); and always otherwise: when either is a
directory (including a file replaced by a directory, or the reverse) or a
file that is not UTF-8 text, since no line measure applies. For persona files and
skills this changes nothing (their add and remove are already T2); it
matters for resources, whose add and remove are T1.

**Guardrail touch.** Detected by the verifier integrity rules
(07-verification.md); this contract only fixes its consequence.

### 6.4 Rules checked when a patch is recorded

- `base` equals the revision it is applied to; every op applies exactly, or
  the patch is rejected (no fuzzy merge).
- Every gene path in the policy and in the patch is in its canonical
  spelling (§2).
- Every op's gene path is covered by `mutable_genes`, overlaps no pin, and,
  for a patch recorded for an Evolution Run, is covered by the run's
  `allowed_genes`.
- `rationale` is non-empty. `evidence` is required for every op except
  `file.edit` on a resource, `memory.retire` and `persona.remove`, which can
  be motivated by the rationale alone.
- The resulting revision is valid against
  [`genome-revision.schema.json`](genome-revision.schema.json), including the
  sort rules of §3.1 and the tree rules of §3.4 (canonical order, unique
  member paths, no member that is also another member's directory).
- **Anything apply would refuse is refused when recording.** A recorded
  candidate must be applicable, so the recorder runs the same per-entry
  predicates apply runs before it writes, on the resulting revision's
  content, and does not rely on the schema for them. They are:

  | Category | Predicate apply uses | Checks |
  | --- | --- | --- |
  | `persona` | `IdentityMaterialiser`, `legal_identity_types` | UTF-8 bytes (including content given by digest); a type the bot's engine accepts |
  | `skills` (`local`) | `SkillPackageValidator`, skills materialiser | One root `SKILL.md` with front matter `name` equal to the entry name and a non-empty `description`; Skill Center limits and path rules |
  | `resources` | `admission_refusal` (`core/resources/services/file_service.py`) | Every file, and every member of a directory: an allowed extension, at most 500 MB |
  | `resources` | resources materialiser (`apply/materialisers/resources.py`) | With trailing `/` removed, paths unique and none a whole-segment prefix of another (`foo.md` with `foo.md/`, `data/` with `data/a.md`, `foo.md` with `foo.md/b.csv`) |
  | `tools.mcp` | Manifest MCP validation (`schema/entries.py`) | `url` parses with a hostname; header names unique case-insensitively, no CR or LF |
  | `tools.mcp` | `McpMaterialiser.resolve`: `_permitted` (`check_mcp_permission_detail`) and `MCPConfigService.validate_bot_override` | The server exists and the recording actor may enable it; its `config`, if any, is a valid bot override for that server and the bot's engine (for example no remote config on a `LOCAL` server, no platform-managed header, an `endpoint_env` and `transport_protocol` with a matching endpoint) |
  | text files a patch edits | `file.edit`, `skill.update` diffs | Valid UTF-8 before and after |

  When apply gains a new refusal, it belongs in this table too.

  Some of these predicates read state outside the revision: the bot's engine
  (`legal_identity_types`, `validate_bot_override`), the tenant's MCP
  permissions and the MCP registry. The recorder runs them with the
  recording bot's context (its owner, engine, and the actor recording), the
  same context apply would use at that moment. So a recorded revision is one
  that applied when it was recorded; it can still be refused later if that
  state changes (a permission is revoked, a server is retired), and apply
  re-runs every predicate, so such a refusal is an ordinary apply failure,
  not a corrupt revision. Patches cannot change `tools/mcp` (§6.1), so a
  candidate's MCP entries are its parent's, checked when the parent was
  recorded; apply re-checks them at promotion.
  These context checks have no offline negative examples in `examples/`,
  because they need a tenant and an MCP registry; RSI-03 covers them with
  fakes of those services.
- Limits mirror the Manifest: 50 entries per category, 64 KiB of UTF-8 per
  inline content value and per unified diff (a change that needs more is
  sent as whole-file content by digest), at most 500 `file_diffs` in a
  `skill.update`,
  and the fetch-time size limits for skills (100 MiB), files (100 MiB) and
  trees (200 MiB, 5000 files).

## 7. Recording a revision from a Manifest document

A revision is recorded either from a patch (§6) or from a whole Bot Config
Manifest v1 document. The second path:

1. **Parse** the YAML once and validate it with the Manifest's own validator.
2. **Fill** every category the document leaves out from the parent revision
   (or, for the bot's first revision, from the bot's current state, below).
3. **Fetch** every source once, through the Manifest's existing fetch
   machinery (named sources, credentials, placeholders, `unpack`,
   `strip_components`), and store the bytes in the content store.
4. **Build** `spec` from the fetched bytes (§4); a skill's tree is built from
   the files `SkillPackageValidator` keeps (§4.2). Copy the policy from the
   parent (the default policy for a first revision).
5. **Store** the original YAML as the provenance record. It keeps the source
   addresses and comments; it is never hashed.

How fetch details end up:

- **Not needed after the fetch.** Source addresses (git `url`, `ref`,
  `subpath`, `mode`; OSS `bucket`, `key`), the `auth` credential name,
  `on_fetch_failure`, `unpack` and `strip_components` say how to get bytes.
  Once the bytes have a digest, applying the revision needs none of them, so
  they are not in `spec` and not hashed. The same bytes from a moved
  repository give the same revision. A revision cannot refresh itself from
  its sources: refreshing means recording the Manifest again.
- **Placeholders** (`${BOT_ENGINE_TYPE}`, `${BOT_ENV}`, `${BOT_TENANT}`,
  `${BOT_ARCH}`) are resolved once, at record time, with the recording bot's
  values. A revision belongs to one bot, whose engine, environment and tenant
  do not change in normal use, so this gives the same bytes apply would have
  fetched.
- **Inline `content`** is stored in the content store and referenced by
  digest, like fetched content.
- **Script body.** The same placeholder substitution apply performs on a
  script body is done once at record time, and the substituted body is stored.

**First revision: capturing the bot's current state.** Categories the first
document leaves out are captured from what the bot has now. One rule applies
while capturing:

> **The AGENTS.md reference block is removed.** On openclaw bots, writing any
> of `RULES.md`, `OKR.md`, `SAFETY.md` or `OUTPUT.md` makes the platform
> append a fixed block to `AGENTS.md` (`IdentityService.sync_agents_md`,
> `core/services/identity.py`). The block sits between
> `<!-- REFERENCE_FILES_SECTION_START -->` and
> `<!-- REFERENCE_FILES_SECTION_END -->` and lists those four files so the
> engine knows to read them. Manifest apply writes identity files through the
> same service, so the block is present on every openclaw bot that has one of
> those files. It is platform output, not something the owner wrote. If it
> were captured into a revision: it would show up in diffs as owner content;
> a strategy could propose edits to it that apply would overwrite at once;
> and the stored AGENTS.md would differ from the AGENTS.md the owner wrote,
> so the same owner content would get different digests on different bots.
> Capture therefore strips it with `IdentityService._remove_reference_section`,
> the same function `sync_agents_md` uses before re-adding it, and then
> replaces the content with `content.rstrip() + "\n"` (Python's `rstrip()`:
> every trailing space, tab and newline removed, then one `\n`). The
> normalization is needed because `sync_agents_md` writes `content.rstrip()`
> before the block: once the block has been added, the owner's original
> trailing whitespace is gone, so a bot with the block and a bot without it
> would otherwise capture the same owner content with different digests. It
> is applied to every captured AGENTS.md, block or not. Applying the revision
> re-adds the block exactly as today. The running bot is unchanged except
> that, on a bot without the block, trailing whitespace at the end of
> AGENTS.md is trimmed to one newline.

**Ignored and refused.**

- `cli_tools` in a document is ignored: it stays only in the provenance YAML,
  the revision has no CLI tools, and the compiled document omits the
  category, so apply leaves the bot's existing CLI tools as they are.
- `engine_config` in a document is refused (the Manifest refuses it too),
  until #2858.

**Compiling.** Compiling a revision gives a Manifest document whose entries
point at the content store by digest. That source kind is added to the
Manifest by RSI-04, as is a source for Center skills. The mapping of every
field is in [`manifest-v1-compatibility.md`](manifest-v1-compatibility.md).

## 8. Known limitations

| Limitation | Tracking |
| --- | --- |
| A revision pins only the bot-level MCP override; the user-level MCP config and Center defaults are read at apply time, so the same revision can give a different MCP setup later | [#2859](https://github.com/inclusionAI/Avernet/issues/2859) |
| MCP header values are stored as written. A credential put in a header would be kept in revisions, which are never deleted. No production bot does this today | [#2860](https://github.com/inclusionAI/Avernet/issues/2860) |
| `engine_config` is refused | [#2858](https://github.com/inclusionAI/Avernet/issues/2858) |
| Removing a resource from a Manifest does not remove it from the bot, so going back to a revision without a resource leaves it in place | [#2162](https://github.com/inclusionAI/Avernet/issues/2162) |
| Editing one file of a directory stores a new blob for the whole tree | Open decision G-1 |
| CLI tools are not part of a revision; compiled documents leave them untouched | acli work |
| `script: null` in a Manifest document is read as "absent" by apply, so a compiled document cannot remove a bot's script | [#2862](https://github.com/inclusionAI/Avernet/issues/2862) |
