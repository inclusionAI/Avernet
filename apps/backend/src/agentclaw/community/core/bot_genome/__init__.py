"""Bot Genome Registry (work item RSI-03).

Immutable, content-addressed revisions of a bot's definition, the refs that
point at them, the patches that change them, and the promotion that hands a
revision to delivery. The record format is the RSI-02 contract in
``apps/backend/api-contracts/genome/v1/``; a copy of its schema files lives in
``contract/`` so the registry can validate at runtime from the package.

Layout:

- ``spec_models`` / ``patch_models`` — the contract's records as classes
  (``GenomeSpec``, ``GenomePolicy``, ``GenomePatch`` and its ops), shared by the
  core and the HTTP surface;
- ``canonical`` / ``gene_paths`` / ``rewrite`` / ``text_edits`` — pure helpers
  the contract defines (RFC 8785 subset, gene-path grammar, the rewrite
  measure, the ``file.edit`` kinds);
- ``patch/`` — applying a patch to a spec, the policy checks, risk tiers;
- ``spec_checks`` / ``diff`` / ``compile`` — the per-revision predicates apply
  runs, the diff between two specs, the compiled desired-state document;
- ``capture/`` — reading a bot's current state into a spec;
- ``registry`` / ``promotion/`` — the two services and the delivery seam;
- ``repository/`` + ``sql/`` — the five ``ac_genome_*`` tables.

See ``README.md`` for the design and the context boundary.
"""
