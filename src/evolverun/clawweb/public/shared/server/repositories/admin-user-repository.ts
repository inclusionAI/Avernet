import type { IDatabase } from "../db.js";

export type AdminRole = "admin" | "log_admin" | "bench_admin" | "claw_evolve_admin" | "claw_insight_admin";

export const ADMIN_ROLES: readonly AdminRole[] = [
  "admin",
  "log_admin",
  "bench_admin",
  "claw_evolve_admin",
  "claw_insight_admin",
];

export type AdminUserRow = {
  id: number;
  user_id: string;
  role: AdminRole;
  source: string;
  enabled: number;
  created_by: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type AdminUserSet = {
  admins: Set<string>;
  logAdmins: Set<string>;
  benchAdmins: Set<string>;
  clawEvolveAdmins: Set<string>;
  clawInsightAdmins: Set<string>;
};

export type AdminUserLists = Record<AdminRole, readonly string[]>;

function normalizeUserId(userId: string): string {
  return userId.trim().toLowerCase();
}

/**
 * Reads the dynamic admin roster stored in `clawweb_admin_users`.
 *
 * The table is authoritative at request time; the YAML `auth` lists are seeded into it at
 * startup and remain the fallback used when this repository is unavailable.
 */
export class AdminUserRepository {
  constructor(private readonly db: IDatabase) {}

  async listEnabled(): Promise<AdminUserSet> {
    const rows = await this.db.query<{ user_id: string; role: AdminRole }>(
      `SELECT user_id, role FROM clawweb_admin_users WHERE enabled = 1`,
    );
    return rows.reduce<AdminUserSet>(
      (acc, row) => {
        const userId = normalizeUserId(row.user_id);
        switch (row.role) {
          case "admin":
            acc.admins.add(userId);
            break;
          case "log_admin":
            acc.logAdmins.add(userId);
            break;
          case "bench_admin":
            acc.benchAdmins.add(userId);
            break;
          case "claw_evolve_admin":
            acc.clawEvolveAdmins.add(userId);
            break;
          case "claw_insight_admin":
            acc.clawInsightAdmins.add(userId);
            break;
          default:
            break;
        }
        return acc;
      },
      {
        admins: new Set<string>(),
        logAdmins: new Set<string>(),
        benchAdmins: new Set<string>(),
        clawEvolveAdmins: new Set<string>(),
        clawInsightAdmins: new Set<string>(),
      },
    );
  }

  async hasRole(candidates: readonly string[], role: AdminRole): Promise<boolean> {
    const normalized = [...new Set(candidates.map((id) => normalizeUserId(id)).filter(Boolean))];
    if (normalized.length === 0) return false;
    const placeholders = normalized.map(() => "?").join(",");
    const rows = await this.db.query<{ count: number }>(
      `SELECT COUNT(*) as count FROM clawweb_admin_users
       WHERE user_id IN (${placeholders}) AND role = ? AND enabled = 1`,
      [...normalized, role],
    );
    return Number(rows[0]?.count ?? 0) > 0;
  }

  /**
   * Idempotently seed the configured lists into the database.
   * Existing rows are left untouched, so runtime grants are never clobbered by YAML.
   */
  async seedFromYaml(lists: AdminUserLists, createdBy?: string): Promise<void> {
    const now = this.db.dialect.now();
    const isMysql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
    for (const role of ADMIN_ROLES) {
      for (const rawUserId of lists[role] ?? []) {
        const userId = normalizeUserId(rawUserId);
        if (!userId) continue;
        try {
          if (isMysql) {
            await this.db.exec(
              `INSERT IGNORE INTO clawweb_admin_users (user_id, role, source, enabled, created_by, gmt_create, gmt_modified)
               VALUES (?, ?, 'yaml_seed', 1, ?, ?, ?)`,
              [userId, role, createdBy ?? null, now, now],
            );
          } else {
            await this.db.exec(
              `INSERT INTO clawweb_admin_users (user_id, role, source, enabled, created_by, gmt_create, gmt_modified)
               VALUES (?, ?, 'yaml_seed', 1, ?, ?, ?)
               ON CONFLICT (user_id, role) DO NOTHING`,
              [userId, role, createdBy ?? null, now, now],
            );
          }
        } catch (err) {
          console.warn(
            `[AdminUserRepository] Failed to seed admin ${userId}/${role}: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    }
  }
}
