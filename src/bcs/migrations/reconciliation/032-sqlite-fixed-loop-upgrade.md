# SQLite Fixed Loop 028 upgrade

The pre-consolidation `028_fixed_loop_execution_plan` migration was applied to
retained local databases before the draft was combined into
`028_fixed_loop_runtime`. Renaming the version changed its version/name checksum
and blocked startup. Having no Git commit did not make that database disposable.

## Accepted history

The compatibility path accepts exactly version 28, dialect `sqlite`, name
`fixed_loop_execution_plan`, and checksum
`7b918552e72f1a1c89049ed10ca6bd2f24952f81bed339c5005955d9758d7a58`.
All three `execution_plan_*` columns must already exist as nullable TEXT on
`bcs_state_machine_definition_snapshots`. Missing or changed columns, another
name/dialect/checksum, and unrelated checksum mismatches still reject startup.

The original 028 row, including its checksum and `applied_at`, is never updated
or deleted. Migration 032 completes the missing runtime schema and records its
own success. Numbers 029–031 are not reused because the split draft used them.

The existing combined-028 identity
`52a78448b5484695fbebef115a3294a54a8ca32b8d51661c3816813d0c5ce092`
is unchanged. SQLite hashes version/name, not SQL contents, so some earlier
combined-028 databases may have an incomplete checkpoint table despite this
matching checksum. Migration 032 also fills those missing columns.

## Upgrade

1. Stop BCS and concurrent migration processes; take a consistent SQLite backup
   including any WAL contents (use SQLite's backup API or `.backup`).
2. Build the updated `bcs` and `bcs-admin` binaries.
3. Inspect and apply with the explicit retained database path:

   ```sh
   bcs-admin db migrate --dialect sqlite --sqlite-path /path/to/bcs.db --check-db
   bcs-admin db migrate --dialect sqlite --sqlite-path /path/to/bcs.db --apply --yes
   bcs-admin db migrate --dialect sqlite --sqlite-path /path/to/bcs.db --check-db
   ```

4. The final check must show target/current version 32 and no pending versions.
   Verify the original 028 metadata and retained data against the backup, then
   restart the updated BCS. Startup uses the same upgrade runner.

032 first reuses the frozen 028 DDL with column-existence guards to add missing
snapshot/Node fields, indexes and the checkpoint table. Its supplementary SQL
adds missing columns to an existing opening/dispatch checkpoint table. Run 032
through the Rust runner; its SQL file alone is not the complete upgrade, and
unguarded ALTER statements are not intended for direct repeated execution.
Fresh and fully upgraded schemas skip existing columns.

Every write failure aborts the upgrade before recording 032; a retry resumes
completed DDL. Existing snapshot, Node and checkpoint values are preserved;
nullable new fields stay NULL and new lease tokens start at zero. No workflow
facts are reconstructed and neither v2 execution nor the scanner is enabled.

Keep 028 and 032 frozen after this repair. Further schema additions need a later
version, including while the work is still uncommitted. Downgrade requires
draining active workflows and a compatible binary; restoring a backup discards
any writes after that backup and is not an automatic rollback step.
