# Manifest Sources: an explicit protocol axis, and `resources` over git

Follow-up to W6 (`resources`) and W7 (named + git sources) of
`docs/bot-config-manifest/work-items.zh-CN.md`. Plan: `plan.md` in this
directory.

## Summary

A manifest declares **where content comes from**. Today that "where" is spelled
by which key a source happens to carry — `git:` or `url:` — and which
(category, source shape) pairs actually work is scattered across per-category
validator branches, materialiser code, and prose in three documents that
disagree with each other and with the code.

This change makes the source axis **explicit and enumerable**:

1. A source declares a `protocol`. Exactly two exist: **`git`** and **`oss`**.
2. **`resources` gains git and named sources.** It is currently the one
   fetching category locked to inline sources only.
3. The **(category × protocol) support matrix becomes one declarative table in
   code**, read by the `PUT` validator *and* published by the capabilities
   endpoint, with a test that drives a real request per cell and asserts the
   observed outcome equals the table.

The third item is the one that keeps the other two honest. Every defect below
exists because no single artifact answers "what works with what".

## Motivation

Five defects, each reproduced against the running validator, not inferred:

**D1 — `resources` cannot use a named source at all.** Not just git: a
`from:` pointing at a *URL* source is refused too, with `unsupported_source`.
Resources is limited to inline `source:`/`content:`, so it cannot participate
in the declare-once-reference-many mechanism that is the stated point of named
sources. Bumping one `ref:` to upgrade a bot's whole content set — the
feature's headline property — silently excludes workspace resources.

**D2 — the published example is not accepted.** `examples.zh-CN.md` §1 shows
`resources` entries with `from: content`. `PUT` returns 422. Its own caveat
header is stale in the other direction as well: it lists `cli_tools` and
`from`/git as "not yet open" when all three are, and never mentions the one
combination that is genuinely closed.

**D3 — entry-level `subpath` on a git source is accepted at `PUT` and fails at
apply.** The error arrives only in the apply report:

> entry-level 'subpath' is not supported on a git source in v1 — declare
> 'subpath' on the source itself

Because `subpath` is what distinguishes one entry from another, a git source
can serve exactly one entry. Two files from one repo need two source blocks
carrying duplicate `url`/`ref`/`auth` — reintroducing the drift named sources
exist to remove. This is in scope because `resources` over git is not useful
without it: a resources entry names a workspace `path` *and* a source path, and
those differ per entry by construction.

**D4 — no way to reach a private object store.** Every non-git source is an
HTTPS GET with an optional header credential. A bucket requiring AK/SK request
signing is unreachable. `CredentialType.OSS_AKSK` exists in the vocabulary and
is refused at write, and the credential row holds a single `secret`, so the
mechanism has neither storage nor implementation.

**D5 — `cli_tools` digest rule misclassifies named-git.** The rule exempts
`SourceForm.GIT` from the mandatory digest, but a `from:` entry classifies as
`NAMED` even when the source it names is git. So `cli_tools` + `from:` a git
source demands a pin that Appendix C separately calls meaningless on git bytes,
and then fails at apply anyway.

Underneath all five: **the capability model cannot express a (category,
source) pair.** Its rows are flat — one verdict per construct — and the code
says so:

> The one (category, form) pair still undelivered — resources × git/named, the
> URL-only road W6 shipped — is refused per entry at schema validation, with a
> reason that names the category, because a blanket row here cannot.

So the API that exists to answer "what may I write?" structurally cannot answer
it, and callers discover the truth from a 422 or, worse, a failed apply.

## The matrix

The contract this change establishes. Rows are categories, columns are how an
entry names its content.

| category | `content` (inline) | `oss` | `git` |
| --- | --- | --- | --- |
| `identity` | ✅ | ✅ | ✅ |
| `resources` | ✅ | ✅ | ✅ **(new)** |
| `skills` | ❌ not a package | ✅ *(digest required)* | ✅ |
| `cli_tools` | ❌ not an executable | ✅ *(digest required)* | ✅ **(digest not required)** |
| `mcp` | — registry reference only, no source axis | | |
| `engine_config` | — no materialiser; whole category refused | | |

Reaching a source **by name** (`from:`) is not a third column: a named source
declares one of the two protocols, and the cell that governs is the protocol's.
`from:` is available wherever the protocol it names is, for every category in
the table.

Two rules the table does not show, stated once here:

- **`digest`** is mandatory for `skills` and `cli_tools` on `oss`, because the
  platform is distributing executable content and an unpinned fetch takes
  whatever is there at the time. On `git` a resolved commit SHA does that job,
  so a digest is not required — **and this holds however the git source is
  reached**, inline or by name (fixes D5).
- **`unpack` / `strip_components`** are `oss`-only. On git the platform holds a
  real tree and `subpath` selects it, so both fields are inert. Declaring
  either on a git source is **refused at `PUT`** rather than silently ignored —
  a field that appears to configure something and does nothing is the failure
  mode D3 and Appendix C's `cli_tools` trap both have.

## Behaviour changes

### Sources declare a protocol

```yaml
schema_version: 1

sources:
  content:
    protocol: git
    url: https://code.example-corp.com/team/content.git
    ref: v1.2.0            # tag, branch, or a full commit SHA
    auth: corp-git-content
  artifacts:
    protocol: oss
    url: https://artifacts.example-corp.com/tools/
    auth: oss-artifacts
```

`ref` takes **a tag, a branch, or a commit SHA**. Whichever is written, the
platform resolves it to one commit per `(url, ref)` per apply and records that
commit in the apply report's `sources[]` row as `resolved_sha` — the answer to
"which version is actually running". A tag or branch can move between applies;
`mode: strict` refuses a ref that resolves to a different commit than the last
apply saw, `non_strict` (the default) allows it and reports the move. Writing a
commit SHA as the `ref` is the way to pin absolutely.

An entry names a source and, where the protocol addresses a tree, the part it
wants:

```yaml
manifest:
  resources:
    - path: data/faq.csv       # one file, from git
      from: content
      subpath: kb/faq.csv
    - path: data/kb/           # a whole tree, from git — no packaging
      from: content
      subpath: kb/
    - path: data/pricing.csv   # one object, from oss
      from: artifacts
      subpath: reference/pricing.csv
```

A `path` ending in `/` is a **directory entry**: the source subtree is
delivered under it **recursively — every file at every depth**, and the
declared area is replaced wholesale, so a file that disappears upstream
disappears from the workspace on the next apply. A `path` not ending in `/` is
a single file.

**This replaces the `git:`/`url:` spelling; `schema_version` stays `1`.** The
feature is pre-release and self-tested, so there is no installed base to carry:
a stored document using the old spelling is refused at `PUT` with a message
naming the `protocol` form. Adding a second schema version to preserve a
spelling nobody depends on yet would buy a migration burden and no safety.

### `subpath` becomes an entry-level selector on every protocol

An entry may declare `subpath` against a git source; it selects within the
tree the source resolved. A source may still declare its own `subpath`, and the
two compose (source's, then entry's). This is what lets one source serve many
entries — the D3 fix, and the precondition for `resources` over git.

### OSS

`protocol: oss` fetches through a guarded object-store road with the same
ceilings, timeouts and prefix authorization the HTTPS road enforces.
`CredentialType.OSS_AKSK` becomes writable and carries **two** values — an
access key id and a secret — and the fetcher signs requests with them. The
credential's `allowed_prefixes` continue to bound where it may be presented,
matched on whole path segments.

Read-back stays redacted: a caller learns that a secret is stored, its type,
and its scopes — never a value, in any response, log or apply report.

### The capabilities endpoint answers the matrix

`GET …/config-manifest/capabilities` gains a per-cell view alongside the
existing `constructs` array, so a client can decide what to write **before**
writing it, for a combination rather than a construct. The existing array keeps
its shape and meaning; this is additive.

## Acceptance criteria

1. A manifest declaring `protocol: git` and `protocol: oss` sources is
   accepted, and entries in `identity`, `skills`, `resources` and `cli_tools`
   resolve through them.
2. `resources` accepts `from:` and git sources: a **file** entry writes one
   file at its `path`; a **directory** entry (`path` ending in `/`) writes the
   source subtree under it **recursively, every file at every depth**, with no
   `unpack` declared, replacing the declared area wholesale.
3. Two entries referencing **one** named git source with different `subpath`
   values both resolve, and the apply reports one `sources[]` row with a single
   `resolved_sha` shared by both.
4. `unpack` or `strip_components` on a git source is refused at `PUT`, naming
   the field and the protocol.
5. `cli_tools` + `from:` a git source is accepted **without** a `digest`, and
   applies.
6. A `type: oss_aksk` credential can be registered with both values, is never
   readable back, and an `oss` source using it fetches from a private bucket.
   A source outside the credential's `allowed_prefixes` is refused.
7. A source using the old `git:`/`url:` spelling is refused at `PUT` with a
   message naming the `protocol` form — no silent acceptance, no dual road.
8. **The matrix is one table in code.** The validator's refusals and the
   capabilities endpoint's answers both derive from it, and a test drives a
   real `PUT` per cell and asserts the outcome matches the table. Adding a
   category or a protocol without a verdict fails at import, not silently.
9. `examples.zh-CN.md`, `user-manual.zh-CN.md` (§5.3, Appendix C, §10) and
   `manifest-schema.zh-CN.md` §7 describe what the code does, and the manual's
   §10 limits table gains the source-URL cap it omits today.

## Out of scope

- **`engine_config`** stays refused; it has no materialiser and this change
  does not give it one.
- **`mcp`** keeps its registry reference and gains no source axis.
- **New protocols** beyond `git` and `oss`. The axis is built to extend; this
  change adds no third member.
- **`categories[].aborted` semantics.** The report field means "abandoned at
  resolve/plan" while §B.2.5 defines it as "an entry failed"; a per-entry
  failure from `write` leaves it false. Real, separately tracked, not fixed
  here.
- **A second schema version.** `schema_version` stays `1`; the protocol axis
  replaces the old spelling in place. See "Decisions taken".

## Decisions taken

- **No schema versioning — the spelling changes in place.** An earlier draft
  introduced `schema_version: 2` so v1 documents kept applying. Withdrawn on
  review: the feature is pre-release and self-tested, so there is no installed
  base to protect, and a compatibility layer for a spelling nobody depends on
  yet is pure carrying cost. Old-spelling sources are refused at `PUT`.
- **`oss` covers plain HTTPS object fetches**, authenticated or not. A public
  CDN file is an `oss` source with no `auth`. The alternative — a third `url`
  protocol — was declined in favour of the two-protocol vocabulary.
- **D3 (entry-level `subpath`) is in scope**, though it was not one of the two
  asked-for items: `resources` over git cannot be useful without it, and it is
  a live apply-time failure for `identity` and `skills` today.
- **D5 (`cli_tools` digest on named-git) is in scope** because the matrix makes
  the rule explicit, and shipping a matrix that the code contradicts in one
  cell would defeat the point.
