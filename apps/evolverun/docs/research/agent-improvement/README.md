# Agent improvement and evaluation research

This directory contains public research notes that explain how improvement and
evaluation work can fit the existing Avernet architecture. These notes are not
runtime configuration, production policy, or a second evolution artifact.

## Status

- `research-draft`: source material is being consolidated and still needs
  domain-owner review.
- `proposed`: the note is coherent enough to discuss as a design proposal.
- `accepted`: the relevant decision or specification has been reviewed and
  adopted; a research note remains as historical context.

Research notes must state which claims are design proposals, which are backed by
implementation evidence, and which still require an execution or acceptance
check.

## Topics

- [`rsi/outer-loop-integration.md`](rsi/outer-loop-integration.md): how an
  outer improvement loop can exchange evidence with the existing Bot Genome,
  evaluation, and promotion boundaries.
- [`rsi/candidate-version-binding.md`](rsi/candidate-version-binding.md): evidence
  for binding harness, model, environment, and observed execution identities.
- [`rsi/evaluation-data-boundaries.md`](rsi/evaluation-data-boundaries.md): a
  research boundary for training, development evaluation, and independent
  acceptance data.
- [`benchmark-question-meta.md`](benchmark-question-meta.md): how canonical
  question metadata is projected separately to the Actor and Judge.

- [`benchmark/environment-reproducibility.md`](benchmark/environment-reproducibility.md):
  reproducible environment boundaries, actor-visible evidence, and future
  admission checks without exposing private evaluation material.

The accepted decisions remain authoritative:

- [ADR 0019: Bot Genome is the unit of evolution](../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md)
- [ADR 0020: Promotion is platform-owned](../../../../../docs/adr/0020-promotion-is-platform-owned.md)

Future notes should extend or map these decisions rather than introduce a
parallel Genome, promotion path, or experiment ledger.

- [`benchmark/taxonomy-versioning.md`](benchmark/taxonomy-versioning.md): taxonomy
  snapshot binding, reviewed migration, and unique-question versus label-hit
  coverage accounting.
