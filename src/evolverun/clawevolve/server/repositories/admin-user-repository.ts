import type { IDatabase } from "../db.js";

export type AdminRole = "admin" | "log_admin" | "bench_admin" | "claw_evolve_admin";

export const ADMIN_ROLES: readonly AdminRole[] = [
  "admin",
  "log_admin",
  "bench_admin",
  "claw_evolve_admin",
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
};

export type CreateAdminUserInput = {
  userId: string;
  role: AdminRole;
  source?: string;
  createdBy?: string;
};

const SELECT_COLUMNS = `id, user_id, role, source, enabled, created_by, gmt_create, gmt_modified`;

function normalizeUserId(userId: string): string {
  return userId.trim().toLowerCase();
}

export class AdminUserRepository {
  constructor(private db: IDatabase) {}

  async listAll(role?: AdminRole): Promise<AdminUserRow[]> {
    if (role) {
      return this.db.query<AdminUserRow>(
        `SELECT ${SELECT_COLUMNS} FROM clawweb_admin_users WHERE role = ? ORDER BY role, user_id`,
        [role],
      );
    }
    return this.db.query<AdminUserRow>(
      `SELECT ${SELECT_COLUMNS} FROM clawweb_admin_users ORDER BY role, user_id`,
    );
  }

  async listEnabled(): Promise<AdminUserSet> {
    const rows = await this.db.query<{ user_id: string; role: AdminRole }>(
      `SELECT user_id, role FROM clawweb_admin_users WHERE enabled = 1`,
    );
    return rows.reduce<AdminUserSet>(
      (acc, row) => {
        const userId = row.user_id;
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
      },
    );
  }

  async find(userId: string, role: AdminRole): Promise<AdminUserRow | null> {
    const rows = await this.db.query<AdminUserRow>(
      `SELECT ${SELECT_COLUMNS} FROM clawweb_admin_users WHERE user_id = ? AND role = ?`,
      [normalizeUserId(userId), role],
    );
    return rows[0] ?? null;
  }

  async add(input: CreateAdminUserInput): Promise<AdminUserRow> {
    const now = this.db.dialect.now();
    const isMysql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
    if (isMysql) {
      await this.db.exec(
        `INSERT INTO clawweb_admin_users (user_id, role, source, enabled, created_by, gmt_create, gmt_modified)
         VALUES (?, ?, ?, 1, ?, ?, ?)
         ON DUPLICATE KEY UPDATE
           enabled = 1,
           source = VALUES(source),
           created_by = COALESCE(VALUES(created_by), created_by),
           gmt_modified = VALUES(gmt_modified)`,
        [
          normalizeUserId(input.userId),
          input.role,
          input.source ?? "manual",
          input.createdBy ?? null,
          now,
          now,
        ],
      );
    } else {
      await this.db.exec(
        `INSERT INTO clawweb_admin_users (user_id, role, source, enabled, created_by, gmt_create, gmt_modified)
         VALUES (?, ?, ?, 1, ?, ?, ?)
         ON CONFLICT (user_id, role) DO UPDATE SET
           enabled = 1,
           source = excluded.source,
           created_by = COALESCE(excluded.created_by, clawweb_admin_users.created_by),
           gmt_modified = excluded.gmt_modified`,
        [
          normalizeUserId(input.userId),
          input.role,
          input.source ?? "manual",
          input.createdBy ?? null,
          now,
          now,
        ],
      );
    }
    const row = await this.find(input.userId, input.role);
    return row!;
  }

  async remove(userId: string, role: AdminRole): Promise<boolean> {
    const result = await this.db.exec(
      `DELETE FROM clawweb_admin_users WHERE user_id = ? AND role = ?`,
      [normalizeUserId(userId), role],
    );
    return result.affectedRows > 0;
  }

  async removeById(id: number): Promise<boolean> {
    const result = await this.db.exec(
      `DELETE FROM clawweb_admin_users WHERE id = ?`,
      [id],
    );
    return result.affectedRows > 0;
  }

  async countAdmins(role: AdminRole): Promise<number> {
    const rows = await this.db.query<{ count: number }>(
      `SELECT COUNT(*) as count FROM clawweb_admin_users WHERE role = ? AND enabled = 1`,
      [role],
    );
    return Number(rows[0]?.count ?? 0);
  }

  /**
   * Check whether any of the provided candidate identifiers has the given role
   * in the database. Candidate matching is case-insensitive and trimmed.
   */
  async hasRole(candidates: string[], role: AdminRole): Promise<boolean> {
    if (candidates.length === 0) return false;
    const normalized = [...new Set(candidates.map(normalizeUserId).filter(Boolean))];
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
   * Idempotently seed admin lists into the database from configuration.
   * Existing rows are left untouched (source remains whatever it was).
   * Only inserts missing rows with source='yaml_seed'.
   */
  async seedFromYaml(
    lists: Record<AdminRole, readonly string[]>,
    createdBy?: string,
  ): Promise<void> {
    const now = this.db.dialect.now();
    for (const role of ADMIN_ROLES) {
      const userIds = lists[role] ?? [];
      for (const rawUserId of userIds) {
        const userId = normalizeUserId(rawUserId);
        if (!userId) continue;
        try {
          const isMysql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
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
