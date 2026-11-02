# Local fault injection tools

`record_demo.py` waits for an active speech or vote node and invokes `inject_player.py`, which kills and restarts a matching DSH worker. Pass only player homes; do not include the referee. Both tools accept `--seed` for reproducible selection, `--min-candidates` to require multiple simultaneously running player nodes, and `--queue-config` for an operator-supplied label. The `vote_start` preparation node is excluded. Without a seed, a random seed is generated and saved. Local raw evidence records the candidate identities, actual candidate count and, when available, a read-only snapshot of the delivery policy; a label is not proof of the policy.

Output files contain local runtime identifiers and must stay outside version control. Use generic case filenames: the summary preserves the file stem as its case label. From this tools directory, produce a summary without actor and session fields using:

```bash
python3 summarize_evidence.py --sqlite /path/to/bcs.db /path/to/case-*.json
```

The SQLite file is opened read-only. `kill_to_successor_ms` is an observed time from kill to a running successor, and a missing observation remains null. The recorded delivery status is the last status observed during injection; the optional database checks report its current terminal status. Do not discard failed or incomplete cases.

T3 is the first observed manager tool activity associated with the run (the local tool-call record contains its run ID). It is not the exact model activation start. T4 is a polling observation of a running successor in the same round and phase. The summary preserves the injector's recorded `passed_60s`; it does not independently authenticate the timestamps or verify the full acceptance criteria. Database counts, privacy checks, session/phase correlation and the absence of human prompts must be checked against the raw run records.
