import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type ReleaseInput = {
  workflowId: string; packId: string; snapshotCommit: string; specJson: string;
  minDeployNumber: number; note?: string; botId?: string; ownerId?: string;
};
export type Release = {
  workflowId: string; packId: string; snapshotCommit: string;
  deployNumber: number; version: number; tagName: string; completed: boolean;
};
export class ReleaseConflict extends Error {}
type Stored = { pack_id: string; deploy_number: number; version: number; tag_name: string; action: string; spec_json: string };
const result = (input: Pick<ReleaseInput, "workflowId" | "snapshotCommit">, row: Stored): Release => ({
  workflowId: input.workflowId, snapshotCommit: input.snapshotCommit, packId: row.pack_id, deployNumber: row.deploy_number, version: row.version,
  tagName: row.tag_name, completed: row.action === "deploy",
});

const sqliteQueues = new WeakMap<IDatabase, Promise<unknown>>();

/** DB transactions serialize allocation; Git publication happens outside the transaction. */
export class WorkflowReleaseRepository {
  constructor(private db: IDatabase) {}

  private transaction<T>(fn: (tx: IDatabase) => Promise<T>): Promise<T> {
    if (this.db.dbType !== "sqlite") return this.db.transaction(fn);
    // better-sqlite3 shares one connection and cannot nest BEGIN across async requests.
    const work = (sqliteQueues.get(this.db) ?? Promise.resolve()).then(() => this.db.transaction(fn));
    sqliteQueues.set(this.db, work.catch(() => {}));
    return work;
  }

  private async lock(tx: IDatabase, workflowId: string): Promise<void> {
    const suffix = tx.dbType === "mysql" || tx.dbType === "zdas" ? " FOR UPDATE" : "";
    const rows = await tx.query(`SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?${suffix}`, [workflowId]);
    if (!rows.length) throw new ReleaseConflict("Save workflow before reserving a release");
  }

  private async find(tx: IDatabase, input: Pick<ReleaseInput, "workflowId" | "snapshotCommit">): Promise<Stored | undefined> {
    const rows = await tx.query<Stored>(`SELECT h.pack_id, h.deploy_number, h.version, h.tag_name, h.action, h.spec_json
      FROM workflow_release_reservations r JOIN workflow_deploy_history h
      ON h.pack_id = r.pack_id AND h.workflow_id = r.workflow_id AND h.deploy_number = r.deploy_number AND h.version = r.version
      WHERE r.workflow_id = ? AND r.snapshot_commit = ?`, [input.workflowId, input.snapshotCommit]);
    if (rows.length > 1) throw new ReleaseConflict("Ambiguous release history");
    return rows[0];
  }

  async reserve(input: ReleaseInput): Promise<Release> {
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        return await this.transaction(async tx => {
          await this.lock(tx, input.workflowId);
          const previous = await this.find(tx, input);
          if (previous) {
            if (previous.pack_id !== input.packId || previous.spec_json !== input.specJson) throw new ReleaseConflict("Saved snapshot mismatch");
            return result(input, previous);
          }
          // Include legacy edits, migrations and pending releases, even if they have no Git tag.
          const [max] = await tx.query<{ v: number; n: number }>(`SELECT COALESCE(MAX(version), 0) AS v, COALESCE(MAX(deploy_number), 0) AS n
            FROM workflow_deploy_history WHERE workflow_id = ?`, [input.workflowId]);
          const version = Number(max.v) + 1;
          const deployNumber = Math.max(Number(max.n) + 1, input.minDeployNumber);
          if (version > 2147483647 || deployNumber > 2147483647) throw new ReleaseConflict("Release number exhausted");
          const tagName = `deploy/${input.workflowId}/#${deployNumber}`;
          const now = tx.dialect.now();
          await tx.exec(`INSERT INTO workflow_release_reservations (workflow_id, snapshot_commit, pack_id, deploy_number, version) VALUES (?, ?, ?, ?, ?)`,
            [input.workflowId, input.snapshotCommit, input.packId, deployNumber, version]);
          await tx.exec(`INSERT INTO workflow_deploy_history
            (pack_id, workflow_id, deploy_number, version, tag_name, action, spec_json, note, bot_id, owner_id, is_active, gmt_create, gmt_modified)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, ?, ?)`,
            [input.packId, input.workflowId, deployNumber, version, tagName, input.specJson, input.note ?? null, input.botId ?? null, input.ownerId ?? null, now, now]);
          return { workflowId: input.workflowId, packId: input.packId, snapshotCommit: input.snapshotCommit, deployNumber, version, tagName, completed: false };
        });
      } catch (err) {
        // A legacy writer may not acquire our lock. Retry the entire rolled-back allocation.
        if (attempt < 4 && /Duplicate|UNIQUE constraint|deadlock/i.test(String(err))) continue;
        throw err;
      }
    }
    throw new ReleaseConflict("Release allocation exhausted");
  }

  async complete(input: Omit<Release, "completed">): Promise<Release> {
    return this.transaction(async tx => {
      await this.lock(tx, input.workflowId);
      const row = await this.find(tx, input);
      if (!row || row.pack_id !== input.packId || row.version !== input.version || row.deploy_number !== input.deployNumber || row.tag_name !== input.tagName) {
        throw new ReleaseConflict("Release reservation mismatch");
      }
      if (row.action === "deploy") return result(input, row); // Lost response / repeat request: no reactivation.
      if (row.action !== "pending") throw new ReleaseConflict("Invalid release state");
      await tx.exec(`UPDATE workflow_deploy_history SET action = 'deploy', gmt_modified = ?
        WHERE workflow_id = ? AND pack_id = ? AND deploy_number = ? AND version = ? AND action = 'pending'`,
        [tx.dialect.now(), input.workflowId, input.packId, input.deployNumber, input.version]);
      const [newer] = await tx.query<{ n: number }>(`SELECT COUNT(*) AS n FROM workflow_deploy_history
        WHERE workflow_id = ? AND is_active = 1 AND deploy_number > ?`, [input.workflowId, input.deployNumber]);
      if (!Number(newer.n)) {
        await tx.exec(`UPDATE workflow_deploy_history SET is_active = 0 WHERE workflow_id = ? AND is_active = 1`, [input.workflowId]);
        await tx.exec(`UPDATE workflow_deploy_history SET is_active = 1 WHERE workflow_id = ? AND pack_id = ? AND deploy_number = ? AND version = ?`,
          [input.workflowId, input.packId, input.deployNumber, input.version]);
        await tx.exec(`UPDATE workflow_specs SET version = ? WHERE workflow_id = ?`, [input.version, input.workflowId]);
      }
      return result(input, { ...row, action: "deploy" });
    });
  }
}
