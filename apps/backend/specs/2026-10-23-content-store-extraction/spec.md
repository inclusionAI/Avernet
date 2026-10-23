# Content store as its own component

## Summary

The platform's content-addressed store (bytes by digest plus an append-only
provenance log) becomes a core component of its own instead of a package inside
the Bot Config Manifest. The Manifest and the Genome Registry both use it through
the same contract, and the audit identity width the two of them share is widened
so the registry no longer refuses an identity the Manifest's own columns accept.

## Motivation

The store was built for the Manifest (W11, #1510). The Genome Registry (RSI-03,
#2907) now files every revision's bytes through it and reads them back scoped by
the provenance rows it filed, and uses the Manifest's tree encoding for skills
and directory resources. RSI-04 (direct delivery) will bypass the Manifest for
apply, so the registry and delivery would depend on a package whose apply path
they no longer use. The extraction was deferred in the #2907 review and tracked
as #3064; the plan was approved there on 2026-10-23.

## User Stories

- As a backend contributor working on the Genome Registry or direct delivery, I
  want to depend on the content store without importing the Manifest, so that
  the dependency graph says what the code actually needs.
- As a Manifest contributor, I want the store to keep behaving exactly as it does
  (same fetch receipts, same `keep_last` reads, same provenance rows), so that the
  move is invisible to apply.
- As an operator, I want an application acting for a user with a long id to be
  recorded in the genome audit columns rather than refused, so that the audit
  trail does not depend on the length of a user id.

## Acceptance Criteria

- [ ] The content store (service, contract, scope and record models, errors,
      settings parser, provenance table model, repository contract and
      implementation, DDL) lives under its own core component and nothing in it
      imports the Manifest at runtime.
- [ ] The acm-tree-v1 tree encoding (`canonical_tree_bytes`, `decode_tree_bytes`,
      the magic) lives in the content store component and is no longer exported
      from the Manifest's apply package.
- [ ] The Genome Registry imports the store, the digest grammar, and the tree
      encoding from the content store component only; its declared module
      boundary no longer lists the Manifest's `content` or `apply.entry_delivery`.
- [ ] The Manifest consumes the store through the same contract it did before;
      every existing Manifest, Genome, repository and architecture test passes
      with only import paths and renamed symbols changed.
- [ ] The store's audit identity column and the genome's actor columns share one
      declared width (1100 characters), and an identity of that length is
      recorded rather than refused; one character more is still refused up front.
- [ ] The DDL for the provenance table and the ORM model agree column for column
      and on the audit width; the existing DDL type rules (DATETIME for
      application-filled instants, TIMESTAMP for database-filled ones) still cover
      the moved file.
- [ ] The component is wired at the composition root in its own DI module; the
      yaml key `user_config.bot_config_manifest.content_store_dir` and the table
      name `ac_manifest_content` are unchanged.
- [ ] Renames: `ContentStoreService`, `ContentStoreProtocol`,
      `ContentStoreRepositoryProtocol`, `ContentStoreRepository`.

## In Scope

- Moving the store, its repository, its DDL and its tests to the new component.
- Moving the tree encoding and its tests.
- Widening the provenance `modifier` column to 1100 by editing the existing DDL
  in place (the table is not yet deployed to production, per the issue owner),
  and deriving every audit width from one constant.
- Updating the DI wiring, the schema bootstrap import, the module READMEs
  (context boundaries), and the genome design docs that name the old paths.

## Out of Scope

- The genome contract files (`api-contracts/genome/v1/`), what a revision stores,
  or the Manifest's fetch semantics.
- Renaming the table, the yaml key, or moving `FetchedObject` out of the
  Manifest's fetch package.
- The remaining Genome → Manifest imports (schema limits, capabilities, apply
  outcomes, the apply service's `last_apply`), which RSI-04 owns.
- The Manifest's own `ac_bot_config_manifest.modifier` truncation bound.

## Open Questions

None. Both questions raised on the issue (width, rename) were answered by the
issue owner: edit the SQL in place to 1100, and rename.
