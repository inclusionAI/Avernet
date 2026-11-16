# `evolution_ledger`

RSI experiment history and lineage records (work item 05). The package owns
ledger domain models and persistence-facing behavior; it does not execute a
strategy, verify a candidate, or promote a genome.

## Current implementation

- `models.py` defines the experiment/lineage records used by the evolution
  flow.
- `ledger.py` implements the ledger service boundary consumed by run and
  promotion workflows.
- The runtime binding is assembled by the evolution DI modules.

For the overall design and current implementation/verification status, start
at [`docs/bot-evolution/README.zh-CN.md`](../../../../../docs/bot-evolution/README.zh-CN.md)
and the matching [work items](../../../../docs/bot-evolution/work-items.zh-CN.md).

## Verification boundary

The existence of this module is not evidence that every ledger backend or
production persistence path is wired and live-tested. Check the current unit
and integration tests before changing its storage contract.
