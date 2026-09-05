import type { IDatabase } from "../db.js";

export type BenchTagRow = {
  id: number;
  tag_id: string;
  name: string;
  description: string | null;
  status: string;
  created_by: string | null;
  gmt_create: number | string;
  gmt_modify: number | string;
};

export type BenchDomainTagRow = {
  owner_user_id: string;
  domain_id: string;
  tag_id: string;
  name: string;
  status: string;
};

export type DomainKey = {
  ownerUserId: string;
  domainId: string;
};

const TAG_COLUMNS = "id, tag_id, name, description, status, created_by, gmt_create, gmt_modify";

function nowForBenchTag(): number {
  return Math.floor(Date.now() / 1000);
}

export class BenchTagRepository {
  constructor(private db: IDatabase) {}

  async listTags(includeArchived = false): Promise<BenchTagRow[]> {
    const where = includeArchived ? "" : "WHERE status <> 'archived'";
    return this.db.query<BenchTagRow>(
      `SELECT ${TAG_COLUMNS} FROM cm_bench_tags ${where} ORDER BY status ASC, name ASC`,
    );
  }

  async create(input: {
    tagId: string;
    name: string;
    description?: string | null;
    createdBy?: string | null;
  }): Promise<BenchTagRow> {
    const now = nowForBenchTag();
    await this.db.exec(
      `INSERT INTO cm_bench_tags (tag_id, name, description, status, created_by, gmt_create, gmt_modify)
       VALUES (?, ?, ?, 'active', ?, ?, ?)`,
      [input.tagId, input.name, input.description ?? null, input.createdBy ?? null, now, now],
    );
    const created = await this.findByTagId(input.tagId);
    if (!created) throw new Error("Tag not found after create");
    return created;
  }

  async update(tagId: string, input: {
    name?: string;
    description?: string | null;
    status?: string;
  }): Promise<BenchTagRow | null> {
    const sets: string[] = [];
    const values: unknown[] = [];
    const fields: Array<[string, unknown]> = [
      ["name", input.name],
      ["description", input.description],
      ["status", input.status],
    ];
    for (const [column, value] of fields) {
      if (value !== undefined) {
        sets.push(`${column} = ?`);
        values.push(value);
      }
    }
    if (sets.length === 0) return this.findByTagId(tagId);
    sets.push("gmt_modify = ?");
    values.push(nowForBenchTag(), tagId);
    await this.db.exec(`UPDATE cm_bench_tags SET ${sets.join(", ")} WHERE tag_id = ?`, values);
    return this.findByTagId(tagId);
  }

  async findByTagId(tagId: string): Promise<BenchTagRow | null> {
    const rows = await this.db.query<BenchTagRow>(
      `SELECT ${TAG_COLUMNS} FROM cm_bench_tags WHERE tag_id = ?`,
      [tagId],
    );
    return rows[0] ?? null;
  }

  async listTagsForDomains(keys: DomainKey[]): Promise<BenchDomainTagRow[]> {
    if (keys.length === 0) return [];
    const conds = keys.map(() => "(dt.owner_user_id = ? AND dt.domain_id = ?)");
    const values = keys.flatMap((key) => [key.ownerUserId, key.domainId]);
    return this.db.query<BenchDomainTagRow>(
      `SELECT dt.owner_user_id, dt.domain_id, dt.tag_id, t.name, t.status
       FROM cm_bench_domain_tags dt
       JOIN cm_bench_tags t ON t.tag_id = dt.tag_id
       WHERE (${conds.join(" OR ")}) AND t.status <> 'archived'
       ORDER BY t.name ASC`,
      values,
    );
  }

  async addDomainTags(args: {
    domains: DomainKey[];
    tagIds: string[];
    taggedBy?: string | null;
  }): Promise<number> {
    let affected = 0;
    for (const domain of args.domains) {
      for (const tagId of args.tagIds) {
        const result = await this.db.exec(
          this.db.dbType === "mysql" || this.db.dbType === "zdas"
            ? `INSERT IGNORE INTO cm_bench_domain_tags (owner_user_id, domain_id, tag_id, tagged_by)
               VALUES (?, ?, ?, ?)`
            : `INSERT OR IGNORE INTO cm_bench_domain_tags (owner_user_id, domain_id, tag_id, tagged_by)
               VALUES (?, ?, ?, ?)`,
          [domain.ownerUserId, domain.domainId, tagId, args.taggedBy ?? null],
        );
        affected += result.affectedRows;
      }
    }
    return affected;
  }

  async removeDomainTags(args: {
    domains: DomainKey[];
    tagIds: string[];
  }): Promise<number> {
    if (args.domains.length === 0 || args.tagIds.length === 0) return 0;
    let affected = 0;
    for (const domain of args.domains) {
      const result = await this.db.exec(
        `DELETE FROM cm_bench_domain_tags
         WHERE owner_user_id = ? AND domain_id = ?
           AND tag_id IN (${args.tagIds.map(() => "?").join(",")})`,
        [domain.ownerUserId, domain.domainId, ...args.tagIds],
      );
      affected += result.affectedRows;
    }
    return affected;
  }
}
