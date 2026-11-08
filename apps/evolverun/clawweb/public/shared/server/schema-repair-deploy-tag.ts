/**
 * Self-healing repair for `workflow_deploy_history.tag_name`.
 *
 * Background: v65 already relaxed `tag_name` to nullable for `edit` actions,
 * but managed MySQL environments never executed it. `schema_version` is empty
 * there because DDL is denied to the runtime account, so `runMigrations`
 * bails out early ("DDL denied — assuming schema exists in managed DB") and
 * every migration below the current app version is silently skipped. The
 * physical column therefore stays `varchar(255) NOT NULL`, and any row written
 * before the git tag exists fails with:
 *
 *   Column 'tag_name' cannot be null
 *
 * The application-side fix writes a deterministic tag name instead of null, so
 * the reserve step no longer depends on this column being nullable. This
 * migration repairs the column definition anyway, so other writers that
 * legitimately have no tag (for example `edit` actions) stop failing too.
 *
 * It is marked `selfHealing` so it runs even when the `schema_version` gate
 * reports it as applied — that gate is exactly what is broken on managed
 * MySQL. The repair is idempotent and fail-safe: it introspects the column
 * first, skips the DDL when the column is already nullable, and never throws,
 * because a schema-repair attempt must not block service boot.
 */
export const repairDeployTagMigrations = [
  {
    version: 144,
    description: "Allow null tag_name in workflow_deploy_history (self-healing repair)",
    mysqlOnly: true,
    selfHealing: true,
    sql: [] as string[],
    migrate: async (db: {
      dbType?: string;
      exec: (sql: string) => Promise<unknown>;
      query: <T>(sql: string, params?: unknown[]) => Promise<T[]>;
    }) => {
      if (db.dbType !== "mysql" && db.dbType !== "zdas") return;

      try {
        // Introspect first: skip the DDL entirely when the column is already
        // nullable, so a correctly migrated environment takes no DDL lock.
        const rows = await db.query<Record<string, unknown>>(
          `SELECT IS_NULLABLE FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE()
             AND TABLE_NAME = 'workflow_deploy_history'
             AND COLUMN_NAME = 'tag_name'`
        );
        const row = rows[0];
        const value = row ? (row.IS_NULLABLE ?? row.is_nullable) : undefined;
        if (value === undefined) {
          // Table or column absent — nothing to repair here.
          return;
        }
        if (String(value).toUpperCase() === "YES") return;

        await db.exec(
          "ALTER TABLE `workflow_deploy_history` MODIFY COLUMN `tag_name` varchar(255) DEFAULT NULL COMMENT '关联的git tag名称，edit操作无tag'"
        );
      } catch (err) {
        // Best-effort repair: managed environments may still deny this DDL.
        // The application-side fix keeps deploy working regardless.
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`[clawweb] tag_name repair skipped (non-fatal): ${msg}`);
      }
    },
  },
];