# Migration plan and recovery record

1. Back up the current dirty tree before edits (completed, 2026-11-17).
2. Extract shared Genome contracts, then canonical Evolution packages and import shims.
3. Add standalone configuration/composition/HTTP/worker entrypoints and persistence.
4. Test service isolation, restart recovery, authorization and existing consumers.
5. Add Bot RSI panel and deployment/gate integration; validate separately from live rollout.
6. Record verified results and remaining rollout prerequisites in report.md.

## Backup
Base commit: `0840d21f21218c60882b9bc62dec80c9013d857a`.
A private local archive, tracked binary patch and checksummed manifest were created
and verified before edits. They cover 58 pre-existing changed/untracked files;
`test_llm.py` was intentionally excluded and must remain untouched. The absolute
backup location was reported in the task chat (temporary storage; copy privately
before cleaning temporary files). No archive, credentials or runtime databases
belong in Git.

Recovery: stop consumers, verify manifest checksums, extract the archive into a
separate directory and compare before restoring individual files. Do not reset the
working tree: it contains pre-existing user changes. No commit/push is authorized.

## Completion record

Steps 1–6 for the independent local implementation and compatibility migration are
complete; verified results and the unfulfilled production/Swarm rollout work are
listed separately in [report.md](report.md). No production cutover is implied.
The optional Bot UI remains disabled until its upstream is explicitly configured.
