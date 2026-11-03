# Content store

The platform's own copy of bytes it fetched or produced on a bot's behalf, and
the provenance of every such event. One component behind two consumers:

- the **Bot Config Manifest** (`core/bot_config_manifest/`) files what its
  apply fetches and reads the copy back for delivery, audit and `keep_last`
  (requirement §2.8 of `docs/bot-config-manifest/work-items.zh-CN.md`, W11,
  issue #1510);
- the **Genome Registry** (`core/bot_genome/`) files the bytes a revision names
  (captured files, files a patch wrote, uploads) through `store_produced` and
  reads them back only where a provenance row ties the digest to the bot
  (RSI-03, #2907).

It was built inside the Manifest and moved out as its own component in #3064,
because direct delivery (RSI-04) bypasses the Manifest's apply path while
still reading this store.

## Two halves, one mechanism

- **Bytes** live in a content-addressed blob tree,
  `<root>/blobs/<hex[:2]>/<hex64>`, under
  `user_config.bot_config_manifest.content_store_dir` (parsed by
  `settings.content_store_root_from_config`; the yaml key kept its name when
  the component moved). The digest (`sha256:<hex64>`, `limits.DIGEST_RE`) IS
  the address: identical bytes are written once, the write is durable before
  it is visible (fsync, then `os.replace`), and `read` verifies the hash on
  the same pass it reads — the store hands back bytes it can prove, or fails.
  It never re-fetches.
- **Provenance** lives in `ac_manifest_content`
  (`sql/2026_08_31_manifest_content.sql`, ORM in `models.py`, repository in
  `core/repository/{protocols,implementations}/bot/content_store.py`): an
  append-only log, one row per store event, no unique key — the same digest
  stored again is a new row, and that repetition is the audit fact. A fetched
  row carries the sanitised source and final URLs and a credential *name*; a
  produced row carries `produced://<origin>` as both URLs, so a consumer can
  scope its reads to the rows it filed (`is_reachable(..., produced_by=)`).
- **Retention** is stated, not defaulted: v1 keeps rows and blobs
  unconditionally. The DDL's head comment holds the full reasoning; the one
  sanctioned exception is the blob layer's own `.tmp-*` staging leftovers.

## A stored tree

A directory the platform delivers or records is one blob in the `acm-tree-v1`
framing (`tree.py`): `TREE_MAGIC`, then `<path length>:<path><byte
length>:<bytes>` per member, members sorted by path. `canonical_tree_bytes`
gives a whole tree one stable content address, and `decode_tree_bytes` turns
the blob back into its members (or answers `None` for anything else, an
archive included). The Manifest writes it for git-delivered entries and reads
it back for `keep_last`; the Genome Registry hashes, diffs and patches skill
trees and directory resources in it.

## Widths

`limits.MAX_AUDIT_ACTOR_CHARS` (1100) sizes every audit-identity column the
store and the Genome Registry write: this table's `modifier` and the genome's
`created_actor` / `moved_actor` / `actor_id`. It holds
`app:<bigint>:on-behalf-of:<1024-char user id>`; both the store (before the
blob is written) and the registry (on `Provenance` construction) refuse a
longer identity rather than failing inside a write.

## Context Boundary

```yaml
purpose: Own the platform's content-addressed copy of fetched and produced bytes — the blob tree, the append-only provenance log, the digest grammar, the acm-tree-v1 tree encoding and the audit identity width — behind one Service API the Bot Config Manifest and the Genome Registry both inject.
provides:
  - ContentStoreProtocol  # the Service API both consumers inject
  - ContentStoreService
  - ContentScope
  - StoredContentRecord
  - ManifestContentModel  # the ac_manifest_content ORM row (the table keeps its historical name)
  - ContentStoreError  # 400-class: a malformed address, a value that cannot fit its column
  - ContentMissingError
  - ContentStoreFault  # 500-class: the store's own disk failed
  - ContentIntegrityError
  - DIGEST_RE  # sha256:<hex64>, the address grammar; the Manifest's fetch layer re-exports it
  - MAX_AUDIT_ACTOR_CHARS  # the audit identity width; the genome's limits re-export it
  - TREE_MAGIC
  - canonical_tree_bytes
  - decode_tree_bytes
  - DEFAULT_CONTENT_STORE_DIR
  - content_store_root_from_config
consumes:
  - "ContentStoreRepositoryProtocol (core.repository) — persistence for the append-only provenance log"
  - "FetchedObject (core.bot_config_manifest.fetch.errors) — the receipt type `store` takes; a type-only import, so the store loads without the Manifest's transport stack"
consumed_by:
  - "core.bot_config_manifest — the entry fetch funnel files receipts and reads the copy for delivery and keep_last; materialisers store git-delivered trees in acm-tree-v1"
  - "core.bot_genome — store_produced / is_reachable / read for every byte a revision names; the tree encoding for skills and directory resources; MAX_AUDIT_ACTOR_CHARS for its actor columns"
  - "di/modules/content_store_module — binds the repository and provides the store from the typed manifest config cluster"
  - "RSI-04 (direct delivery) — reads compiled revisions' bytes from here with no Manifest document in between"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_config_manifest.fetch.errors  # FetchedObject, TYPE_CHECKING only
  - agentclaw.community.core.repository
  - agentclaw.community.log
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

A change to the store's addressing, framing or provenance shape reaches both
the Manifest's apply (every fetched entry, `keep_last`) and the Genome
Registry (every revision's bytes, the reachability rule, the digests that
make a revision id). A change to `MAX_AUDIT_ACTOR_CHARS` is a DDL change on
`ac_manifest_content` and the genome's actor columns together.
