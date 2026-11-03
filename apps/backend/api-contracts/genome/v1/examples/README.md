# Genome contract examples

Every digest and revision id in these files is real: the bytes behind each
digest are in [`blobs/`](blobs/), named by their hex digest, and each revision
id is the hash of that revision's `{spec, policy}` (README §3.2). The contract
test (`tests/community/contracts/test_bot_genome_schema.py`) checks all of it.

| Directory | Contents |
| --- | --- |
| [`blobs/`](blobs/) | Stored bytes, one file per digest: persona files, `acm-tree-v1` trees, memory item lists, script bodies, uploads |
| [`revisions/`](revisions/) | Valid revisions: a full personal bot, a candidate produced by a patch, a bot with curated memory, a `claude_code` bot, a bot with a shared-repository skill |
| [`patches/`](patches/) | `00-mixed-r1-to-r2.json` (the patch that turns `revisions/01` into `revisions/02`), then one patch per op kind, each covering every field and value shape that op allows (inline content and `{digest}`, null deletes, every `file.edit` kind and target) |
| [`invalid/`](invalid/) | Records that must be refused: `{schema, check, reason, record}`, where `check` is `schema` (fails JSON Schema), `order` (fails the sort and uniqueness rules, README §3.1), `canonical-path` (a gene path not in its canonical spelling, README §2), `tree` (an `acm-tree-v1` blob that is not canonical, README §3.4), `skill-package` (a local skill apply would refuse, README §4.2), `utf8` (a persona file that is not UTF-8, README §4.2), `admission` (a resource file the workspace does not admit, README §4.2), `resource-paths` (resource paths that alias or lie beneath one another, README §4.2) or `identity` (wrong revision id). Every case other than `schema` and `identity` is schema-valid and has a correct revision id, so it fails only its stated check |
| [`manifest-v1/`](manifest-v1/) | Bot Config Manifest v1 round-trip cases, one per example in `docs/bot-config-manifest/examples.zh-CN.md`; see [`../manifest-v1-compatibility.md`](../manifest-v1-compatibility.md) |

`revisions/02-support-bot-candidate.json` is `revisions/01-support-bot.json`
with `patches/00-mixed-r1-to-r2.json` applied: its `revision.patch_digest` is
the digest of that patch's canonical JSON, and its `parent` is `revisions/01`.

The per-op patches are written against `revisions/01` (or `revisions/03` for
memory ops), and every text edit in them (`insert_after`, `replace_section`,
and each diff) was checked to apply exactly to that base when they were
generated. The contract test checks their shape, and the Genome Registry
(RSI-03) will use them as fixtures for patch application.

To change an example, edit it and update every digest and id it affects. When
an id is wrong, the contract test failure names the expected value.
