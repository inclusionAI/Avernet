# Execution plan

1. Add `--auto-restart` as an opt-in `start` flag requiring `--daemon`; add a hidden internal supervisor entry point.
2. Preserve the existing worker startup and IPC, adding a separate supervisor lock, instance record and instance-scoped stop record.
3. Poll verified worker state, restart an exited worker, back off repeated startup failures and reap worker children.
4. Make `stop` disable the supervisor and let it finish worker shutdown; fence registration while either lifecycle owner is active.
5. Expose supervisor state in text/JSON status and document the restart boundary.
6. Add unit and Unix integration coverage, then run the complete public bridge crate suite.
