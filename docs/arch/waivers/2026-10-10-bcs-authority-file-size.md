# File-size exception record: BCS-AUTHORITY-SIZE-2026-10-10

| Field | Value |
| --- | --- |
| Waiver ID | `BCS-AUTHORITY-SIZE-2026-10-10` |
| Scope | `apps/bcs/crates/services/**` source files introduced by the Bot owner/manager authority feature (PR #2625) |
| Ceiling | 2,500 physical lines per source file; the standard 1,000-line rule resumes below it |
| Owner | zhangwu.zh |
| Approved by | BCS architecture owners (tracked in PR #2625 review) |
| Granted | 2026-10-10 |
| Expires | 2026-11-10 (review at expiry; files must be split to standard limits or the waiver must be renewed explicitly) |

## Why

The authority implementation lands as a single atomic feature (shared
SQL/Memory transaction spines, conformance harness, consumer cutover).
Splitting its store modules mid-feature was ruled against during review
because the transaction compositions and their conformance pins are read
most safely as one spine per concern. The ceiling is a hard bound, not a
target: at the time of writing the largest authority file is 2,063 lines
(`authority/transfers.rs`; its typestate and SQL helpers were split into
`authority/transfers_support.rs` to bring it back below the ceiling).

## Conditions

- The ceiling applies only to the scope above. Files approaching it must
  be split by responsibility at the next mechanical boundary, with
  machine-verified content fidelity, exactly like the
  `transfers.rs`/`transfers_support.rs` split.
- No weakening of the file-size CI check. This record is the register the
  allowlist entry references; when it expires the entry is removed or the
  file is split, per the standard waiver policy in `docs/arch/arch.rules.md`.
