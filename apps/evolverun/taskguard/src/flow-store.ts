import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export type RawTaskFlowRecord = Record<string, unknown>;

export type GlobalFlowStore = {
  list: () => Promise<RawTaskFlowRecord[]>;
  get: (flowId: string) => Promise<RawTaskFlowRecord | null>;
  unavailableReason?: string;
};

function defaultRegistryPath(): string {
  return join(homedir(), ".openclaw", "flows", "registry.sqlite");
}

function emptyStore(unavailableReason?: string): GlobalFlowStore {
  return {
    unavailableReason,
    list: async () => [],
    get: async () => null,
  };
}

function rowToFlow(row: Record<string, unknown>): RawTaskFlowRecord {
  return {
    flow_id: row.flow_id,
    sync_mode: row.sync_mode,
    owner_key: row.owner_key,
    controller_id: row.controller_id,
    revision: row.revision,
    status: row.status,
    goal: row.goal,
    current_step: row.current_step,
    state_json: row.state_json,
    wait_json: row.wait_json,
    gmt_create: row.gmt_create,
    gmt_modified: row.gmt_modified,
    ended_at: row.ended_at,
  };
}

export async function createSqliteGlobalFlowStore(dbPath = defaultRegistryPath()): Promise<GlobalFlowStore> {
  if (!existsSync(dbPath)) {
    return emptyStore(`全局 flow registry 不存在: ${dbPath}`);
  }

  try {
    const sqlite = await import("node:sqlite");
    const db = new sqlite.DatabaseSync(dbPath, { readOnly: true });
    return {
      list: async () => {
        const rows = db.prepare(`
          SELECT flow_id, sync_mode, owner_key, controller_id, revision, status, goal,
                 current_step, state_json, wait_json, gmt_create, gmt_modified, ended_at
          FROM flow_runs
          ORDER BY gmt_modified DESC, gmt_create DESC
        `).all() as Array<Record<string, unknown>>;
        return rows.map(rowToFlow);
      },
      get: async (flowId: string) => {
        const row = db.prepare(`
          SELECT flow_id, sync_mode, owner_key, controller_id, revision, status, goal,
                 current_step, state_json, wait_json, gmt_create, gmt_modified, ended_at
          FROM flow_runs
          WHERE flow_id = ?
          LIMIT 1
        `).get(flowId) as Record<string, unknown> | undefined;
        return row ? rowToFlow(row) : null;
      },
    };
  } catch (err) {
    return emptyStore(`全局 flow registry SQLite 不可用: ${err instanceof Error ? err.message : String(err)}`);
  }
}
