/**
 * Repository for cm_bench_domains table.
 */
import type { IDatabase } from "../db.js";

export type BenchDomainRow = {
  id: number;
  domain_id: string;
  name: string;
  description: string | null;
  status: string;
  created_by: string | null;
  owner_user_id: string;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateBenchDomainInput = {
  domainId: string;
  name: string;
  description?: string | null;
  createdBy?: string | null;
  ownerUserId?: string;
};

export type UpdateBenchDomainInput = {
  name?: string;
  description?: string | null;
};

const SELECT_COLUMNS = `id, domain_id, name, description, status, created_by, owner_user_id, gmt_create, gmt_modified`;

export class BenchDomainRepository {
  constructor(private db: IDatabase) {}

  async listAll(ownerUserId?: string, filters?: { status?: string; includeArchived?: boolean }): Promise<BenchDomainRow[]> {
    const statusCondition = filters?.status ? "status = ?" : filters?.includeArchived ? "" : "status = ?";
    const statusValue = filters?.status ?? "active";
    if (ownerUserId) {
      const conditions = ["owner_user_id = ?"];
      const values: unknown[] = [ownerUserId];
      if (statusCondition) {
        conditions.push(statusCondition);
        values.push(statusValue);
      }
      return this.db.query<BenchDomainRow>(
        `SELECT ${SELECT_COLUMNS} FROM cm_bench_domains WHERE ${conditions.join(" AND ")} ORDER BY name`,
        values,
      );
    }
    if (statusCondition) {
      return this.db.query<BenchDomainRow>(
        `SELECT ${SELECT_COLUMNS} FROM cm_bench_domains WHERE ${statusCondition} ORDER BY name`,
        [statusValue],
      );
    }
    return this.db.query<BenchDomainRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_domains ORDER BY name`,
    );
  }

  async findByOwnerAndDomainId(ownerUserId: string, domainId: string): Promise<BenchDomainRow | null> {
    const rows = await this.db.query<BenchDomainRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_domains WHERE owner_user_id = ? AND domain_id = ?`,
      [ownerUserId, domainId],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateBenchDomainInput): Promise<BenchDomainRow> {
    const now = this.db.dialect.now();
    const ownerUserId = input.ownerUserId ?? input.createdBy ?? "";
    await this.db.exec(
      `INSERT INTO cm_bench_domains (domain_id, name, description, status, created_by, owner_user_id, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.domainId,
        input.name,
        input.description ?? null,
        "active",
        input.createdBy ?? null,
        ownerUserId,
        now,
        now,
      ],
    );
    const result = await this.findByOwnerAndDomainId(ownerUserId, input.domainId);
    return result!;
  }

  async update(ownerUserId: string, domainId: string, input: UpdateBenchDomainInput): Promise<BenchDomainRow | null> {
    const existing = await this.findByOwnerAndDomainId(ownerUserId, domainId);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ["name", input.name],
      ["description", input.description],
    ];

    for (const [col, val] of fields) {
      if (val !== undefined) {
        sets.push(`${col} = ?`);
        values.push(val);
      }
    }

    if (sets.length === 0) return existing;

    sets.push("gmt_modified = ?");
    values.push(now);
    values.push(ownerUserId);
    values.push(domainId);

    await this.db.exec(
      `UPDATE cm_bench_domains SET ${sets.join(", ")} WHERE owner_user_id = ? AND domain_id = ?`,
      values,
    );
    return this.findByOwnerAndDomainId(ownerUserId, domainId);
  }

  async archive(ownerUserId: string, domainId: string): Promise<BenchDomainRow | null> {
    const existing = await this.findByOwnerAndDomainId(ownerUserId, domainId);
    if (!existing) return null;
    const now = this.db.dialect.now();
    await this.db.exec(
      `UPDATE cm_bench_domains SET status = ?, gmt_modified = ? WHERE owner_user_id = ? AND domain_id = ?`,
      ["archived", now, ownerUserId, domainId],
    );
    return this.findByOwnerAndDomainId(ownerUserId, domainId);
  }

  async deleteByOwnerAndDomainId(ownerUserId: string, domainId: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM cm_bench_domains WHERE owner_user_id = ? AND domain_id = ?",
      [ownerUserId, domainId],
    );
    return result.affectedRows > 0;
  }
}
