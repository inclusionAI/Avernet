# Plan: Bot Genome schema and patch format (RSI-02)

## Approach

Publish the genome format as checked-in, versioned contract files under
`apps/backend/api-contracts/genome/v1/`, following the BCS precedent
(`apps/bcs/api-contracts/`): JSON Schema (draft-07, like
`kernel/bot_config/artifact.schema.json`) for revisions and patches, the risk
tiers as data, canonical-JSON test vectors, real example records whose
digests and ids are verifiable, and the Manifest v1 compatibility table with
worked round-trip cases. One contract test proves the files agree with each
other and with the design docs. The design docs are then edited to match.
No runtime code. The files are read by three consumers: Backend's Genome
Registry (RSI-03), which validates every revision and patch it records; the
`apps/evolution` service (Evolution Run, Verification), which builds patches
and reads revisions over Backend's HTTP API; and the strategy SDK
(`avernet-evolution-strategy`, 03-strategy.md), the Python library strategy
authors import, which validates a patch locally before submitting it.

## Affected Components

- `apps/backend/api-contracts/genome/v1/` (new) — the normative contract.
- `apps/backend/tests/community/contracts/` — new contract test plus a small
  reference canonicaliser used only by the test.
- `apps/backend/docs/bot-evolution/` — `01-genome`, `03-strategy`,
  `04-default-strategies`, `06-evolution-run`, `08-promotion`, `work-items`,
  each with its `zh-CN` version.
- `apps/backend/specs/2026-10-18-bot-genome-schema/` — this spec; links to
  the contract files.

## Data Model Changes

No database changes. The record formats below change relative to the draft
in `01-genome.md` §2.

### Contract layout

```text
# apps/backend/api-contracts/genome/v1/
README.md                       # versioning, gene path grammar, canonicalisation, recording rules
genome-revision.schema.json     # GenomeRevision = {genome_schema, revision, spec, policy}
genome-patch.schema.json        # GenomePatch and every op
risk-tiers.json                 # op × target → default tier, raises, rewrite threshold
canonical-json-vectors.json     # RFC 8785 vectors + revision-id vectors
manifest-v1-compatibility.md    # every Manifest v1 field → outcome
examples/blobs/<hex64>          # real bytes, so every example digest is checkable
examples/revisions/*.json       # valid revisions (ids are real)
examples/patches/*.json         # one valid patch per op, plus mixed
examples/invalid/*.json         # negative cases with the expected failure
examples/manifest-v1/<case>/    # manifest.yaml → revision.json → compiled.yaml (+ notes.md)
```

### GenomeSpec (changes from the draft)

```jsonc
// spec — every list sorted by its key, keys unique (canonical order)
{
  "persona":   [{"type": "SOUL.md", "digest": "sha256:<hex64>"}],
  "skills":    [{"name": "quality-check", "origin": "local", "digest": "sha256:<tree>"},
                {"name": "refund-policy", "origin": "center", "center_version": "v7"}],
  "memory":    null,                     // {"mode", "items_digest"} once RSI-05 lands
  "resources": [{"path": "data/kb/", "digest": "sha256:<tree>"},   // trailing "/" = directory
                {"path": "faq.csv",  "digest": "sha256:<bytes>"}],
  "tools":     {"mcp": [{"server_code": "mcp.x.meet",
                         "config": {"endpoint_env": "PRE", "transport_protocol": "STREAMABLE_HTTP"}}]},
  "engine_config": {},                   // maxProperties 0 until #2858
  "script":    null                      // or {"digest": "sha256:<body bytes>"}
}
```

- **Key + value.** Keys: `type`, `name`, `path`, `server_code`, memory `key`.
  Value: a digest when there are bytes; inline fields otherwise.
- **`tools.cli_tools` removed**; `tools` holds `mcp` only.
- **`engine_config` stays a required `{}`** so that allowing keys later
  (#2858) leaves every existing revision id unchanged.
- **`origin`**: `local` (bytes in the content store, digest required) or
  `center` (pinned `center_version`, no digest). Where the bytes were fetched
  from (git `url`/`ref`/`subpath`/`mode`, OSS `bucket`/`key`, the `auth`
  credential name) is not part of the spec and not hashed, so the same bytes
  from a moved repository give the same revision. It is kept in the
  provenance copy of the recorded Manifest document.
- **Persona `type`**: `VALID_IDENTITY_FILES`
  (`core/services/identity.py:63`) minus `MEMORY.md` and `IDENTITY.md`. The
  draft's proposed `LESSONS.md` is left out: Manifest identity cannot carry it
  (see Risks). The engine-specific set (`claude_code`: `CLAUDE.md` only) is a
  record-time check, not a schema rule.
- **MCP `config`** is optional: absent means "no bot override". When present
  it is the closed set `{url, headers, endpoint_env, transport_protocol}` with
  the Manifest's own limits (`schema/entries.py:551`). Header names are
  kept as written (review found that not every apply-time merge is
  case-insensitive); `transport_protocol` is upper-cased.

### Digests

`JCS(x)` below is the JSON Canonicalization Scheme (RFC 8785): the single
byte string a JSON value is serialised to before hashing. It is the same rule
the revision id uses (01-genome.md §9.3); the only implementation in this
work item is the test-only reference in Task 2.

```text
file      digest = "sha256:" + hex(sha256(bytes))
directory digest = "sha256:" + hex(sha256(acm-tree-v1(files)))     # skills and directory resources
memory    items_digest = "sha256:" + hex(sha256(JCS(items sorted by key)))
script    digest = "sha256:" + hex(sha256(utf8(body)))
```

`acm-tree-v1` is `canonical_tree_bytes` (`apply/entry_delivery.py:787`):
magic `acm-tree-v1\n`, then `<len>:<path><len>:<bytes>` per file, sorted by
path in code-point order, no compression, no modes, no timestamps, no empty
directories, no symlinks. OSS archives are unpacked (`unpack`,
`strip_components` applied) before encoding, so the digest never depends on
how the archive was compressed.

### GenomePolicy

```jsonc
{
  "mutable_genes":  ["memory", "persona", "resources", "skills"],   // allowlist; sorted
  "pins":           ["persona/SAFETY.md"],                          // write-protected items; sorted
  "risk_overrides": {"persona/SOUL.md": "T3"}                       // raise only
}
```

`locked_genes` is removed: anything not in `mutable_genes` is off limits, and
`policy` is never a valid gene path. An op's tier is the higher of its
default tier and every `risk_overrides` value covering its gene path, so an
override can only raise a tier by construction; a lower value has no effect
and needs no separate check.

### Gene path grammar

```abnf
gene-path = category *( "/" segment )
category  = "persona" / "skills" / "memory" / "resources" / "tools" / "script" / "engine_config"
segment   = 1*( unreserved / pct-encoded )    ; "/" and "%" inside a name are written %2F and %25
```

| Path | Means |
| --- | --- |
| `persona/SOUL.md` | one persona file |
| `skills/refund-policy`, `skills/refund-policy/SKILL.md` | a skill; a file inside it |
| `memory/returns-window` | one memory item |
| `resources/data/kb/faq.md` | a file inside the `data/kb/` resource |
| `tools/mcp/mcp.x.meet` | one MCP server |

Rule `P` covers path `X` iff `X == P` or `X` starts with `P + "/"`. Patch
`target`s use the same grammar, so the policy check is one prefix test.

### GenomePatch ops

| Op | Fields | Default tier |
| --- | --- | --- |
| `file.edit` | `target`, `edits[]` of `replace_section{heading,content}` / `insert_after{anchor,content}` / `diff{diff}` | by target (below) |
| `persona.add` | `type`, `content` (string or `{digest}`) | T2 |
| `persona.remove` | `type` | T2 |
| `skill.add` | `name`, `files{path: string or {digest}}` | T2 |
| `skill.update` | `name`, `file_diffs[]{path,diff}` and/or `files{path: string, {digest} or null}` | T2; T1 when only the SKILL.md frontmatter `description` changes |
| `skill.remove` | `name` | T2 |
| `resource.add` | `path`, `content` or `{digest}` (a directory path takes a tree digest) | T1 |
| `resource.remove` | `path` | T1 |
| `memory.add` / `memory.update` | `item{key,text,tags,source}` | T1 |
| `memory.retire` | `key` | T1 |
| `memory.set_mode` | `mode` | T1 for `seed`/`merge`; T3 for `replace` |

`file.edit` tier by target: `persona/*` T2, `skills/*` same as
`skill.update`, `resources/*` T1. Raises: `rewrite` → at least T2;
`guardrail_touch` → at least T2. No op reaches `tools`, `script`,
`engine_config` or `policy`; those change only by recording a whole Manifest
(owner). Memory ops are rejected while `spec.memory` is `null`.

Rewrite threshold (exact, so two implementations agree):

```text
changed(a, b) = (len(a) + len(b) - 2 * LCS(a, b)) / (len(a) + len(b))   # a, b = line lists
flag rewrite when changed > 0.40                                          # exact LCS, not difflib's ratio
```

## API / Interface Changes

No HTTP or code interfaces change. The contract files are new interfaces;
the versioning rule is:

```text
genome_schema / patch_schema = 1. Any change that alters the canonical bytes of an
existing valid record (new required key, renamed field, new sort rule) bumps the
version; adding a new op or a new optional field that existing records omit does not.
```

## Key Files & Functions

```jsonc
// api-contracts/genome/v1/genome-revision.schema.json (sketch)
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://avernet/api-contracts/genome/v1/genome-revision.schema.json",
  "type": "object", "additionalProperties": false,
  "required": ["genome_schema", "revision", "spec", "policy"],
  "properties": {"genome_schema": {"const": 1}, "revision": {"$ref": "#/definitions/revisionMetadata"},
                 "spec": {"$ref": "#/definitions/spec"}, "policy": {"$ref": "#/definitions/policy"}},
  "definitions": {"digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                  "safeInt": {"type": "integer", "minimum": -9007199254740991, "maximum": 9007199254740991}}
}
```

```jsonc
// api-contracts/genome/v1/genome-patch.schema.json (sketch): ops discriminated by "op"
{"definitions": {"op": {"oneOf": [{"$ref": "#/definitions/fileEdit"}, {"$ref": "#/definitions/skillAdd"}]}},
 "fileEdit": {"required": ["op", "target", "edits"], "properties": {"op": {"const": "file.edit"}}}}
```

```jsonc
// api-contracts/genome/v1/risk-tiers.json (sketch)
{"version": 1, "rewrite_threshold": {"numerator": 2, "denominator": 5},   // 0.40 without a float
 "rules": [{"op": "file.edit", "target": "persona/*", "tier": "T2"},
           {"op": "memory.set_mode", "when": {"mode": "replace"}, "tier": "T3"}],
 "raises": [{"kind": "rewrite", "min_tier": "T2"}, {"kind": "guardrail_touch", "min_tier": "T2"}]}
```

```python
# tests/community/contracts/genome_canonical_json.py (new, test-only reference)
def canonicalize(value: object) -> bytes: ...          # RFC 8785 subset: no floats, |int| <= 2**53 - 1
def revision_id(spec: dict, policy: dict) -> str: ...  # "sha256:" + sha256(canonicalize({"policy", "spec"}))
def tree_digest(files: list[tuple[str, bytes]]) -> str: ...  # reuses canonical_tree_bytes
```

```python
# tests/community/contracts/test_bot_genome_schema.py (new)
def test_every_valid_example_validates(path): ...
def test_every_invalid_example_fails_with_its_stated_reason(path): ...
def test_canonical_json_vectors(vector): ...
def test_example_revision_ids_and_digests_are_real(path): ...
def test_every_op_in_the_schema_has_a_risk_tier(): ...
def test_risk_tables_in_design_docs_match_risk_tiers_json(doc): ...
def test_manifest_v1_cases_cover_every_examples_doc_fragment(): ...
```

Design-doc tier tables are wrapped in `<!-- risk-tiers:begin -->` /
`<!-- risk-tiers:end -->` markers so the test reads exactly those rows.

Doc edits (each mirrored in `zh-CN`):

```diff
# docs/bot-evolution/01-genome.md
- §2.1 "The gene path grammar is fixed by RSI-02."           + grammar and table (link to contract)
- §2.2 McpServerEntry{server_code, digest}; CliToolEntry      + {server_code, config?}; cli_tools removed
- §2.3 locked_genes / mutable_genes / pins                    + mutable_genes / pins; defaults
- §2.6 seven ops                                              + full op set
- §6.3 tier table                                             + marked table matching risk-tiers.json
+ §4.6 recording rules: placeholders, first-revision capture, AGENTS.md reference block
+ §8   item 6: Center-skill source; dependency on #2162
+ §15  G-1, G-2, G-3, G-7, G-10 closed; limitations #2859, #2860; engine_config #2858
```

- `03-strategy.md`, `04-default-strategies.md`: op examples and gene paths.
- `06-evolution-run.md`: `locked_genes` wording and examples
  (`06-evolution-run.md:529`, `:549`, `:1267`, `:1547`).
- `08-promotion.md`: `PatchOpName` (`:124`), tier table (`:675`), "locked"
  wording.
- `work-items.md`: RSI-02 status; RSI-04 items 6–7.

## Dependencies

None at runtime. Tests use `jsonschema` (already a dev dependency,
`apps/backend/pyproject.toml:116`) and `pyyaml` (already present).

## Risks & Mitigations

- **Risk:** Adding a category later (acli tools, memory delivery) changes the
  canonical bytes of every revision, so ids change.
  **Mitigation:** `engine_config: {}` and `memory: null` are present from v1;
  for genuinely new categories the README states the bump rule and RSI-03
  stores `genome_schema` with each row, so a migration can re-hash.
- **Risk:** Header-name lower-casing changes the bytes sent to an MCP server.
  **Mitigation:** HTTP header names are case-insensitive and apply already
  compares them that way; stated in the compatibility table.
- **Risk:** The interim memory plan (`LESSONS.md` as a persona file,
  01-genome.md §7) is not expressible, because Manifest identity rejects
  `LESSONS.md`.
  **Mitigation:** Excluded from v1 persona types; noted in 01-genome.md §7 as
  needing an identity whitelist change before the interim can be used.
- **Risk:** Contract tests under `tests/community/contracts/` need approval
  from that directory's `OWNERS`.
  **Mitigation:** Called out in the PR description.
- **Risk:** Round-trip is only checked on paper, because `compiled.yaml` uses
  the content-store source RSI-04 has not defined yet.
  **Mitigation:** The proposed source syntax is marked "proposed, fixed by
  RSI-04"; RSI-03/04 reuse `examples/manifest-v1/` as live fixtures.

## Alternatives Considered

- **Draft 2020-12 instead of draft-07:** nicer discriminated unions, but the
  repo's only schema precedent is draft-07; `oneOf` + `const` suffices.
- **`rfc8785` package instead of a reference canonicaliser:** a new
  dependency for a few dozen lines when floats are banned anyway; the RFC's
  own vectors check the implementation.
- **Canonicaliser in `src/` now:** would be runtime code with no caller until
  RSI-03; RSI-03 moves it into `core/bot_genome/` and keeps the vectors.
- **MCP ops at T3:** rejected with the owner; tools change only through whole
  Manifest documents.
- **JSON Patch (RFC 6902):** ops edit file contents inside blobs, not JSON
  pointers, and tiers depend on op meaning.
- **Per-file blobs for directories:** cheaper edits of large trees but a new
  storage design; recorded under G-1.

## Rollout

Docs and contract files only; nothing deploys. Land in one PR.

## Test Strategy

```bash
cd apps/backend && uv run pytest tests/community/contracts/test_bot_genome_schema.py -q
```

- Positive: every file in `examples/revisions`, `examples/patches`,
  `examples/manifest-v1/*/revision.json` validates.
- Negative (`examples/invalid/`): float in hashed content, unsorted list,
  duplicate key, `locked_genes` present, op on `tools/mcp`, `policy` as a
  target, unknown op, CLI tool entry, non-empty `engine_config`, `LESSONS.md`.
- Vectors: RFC 8785 §3.2.3 key-ordering example and string-escaping cases;
  revision-id vectors computed from example spec/policy pairs.
- Consistency: tier coverage for every op in the schema; design-doc tables
  equal `risk-tiers.json`; every fragment in `examples.zh-CN.md` has a case.
