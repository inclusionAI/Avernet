/**
 * Repository for cm_bench_templates table — template identity and current pointers.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type BenchTemplateRow = {
  id: number;
  domain_id: string;
  template_name: string;
  display_name: string | null;
  description: string | null;
  category: string | null;
  target_type: string;
  grading_type: string;
  source: string;
  source_path: string | null;
  source_hash: string | null;
  latest_version: number;
  published_version: number | null;
  status: string;
  created_by: string | null;
  owner_user_id: string;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateBenchTemplateInput = {
  domainId: string;
  templateName: string;
  displayName?: string | null;
  description?: string | null;
  category?: string | null;
  targetType?: string;
  gradingType?: string;
  source?: string;
  sourcePath?: string | null;
  sourceHash?: string | null;
  latestVersion?: number;
  status?: string;
  createdBy?: string | null;
  ownerUserId?: string;
};

export type UpdateBenchTemplateInput = {
  displayName?: string | null;
  description?: string | null;
  category?: string | null;
  targetType?: string;
  gradingType?: string;
  sourcePath?: string | null;
  sourceHash?: string | null;
  latestVersion?: number;
  publishedVersion?: number | null;
  status?: string;
};

const SELECT_COLUMNS = `id, domain_id, template_name, display_name, description, category, target_type, grading_type, source, source_path, source_hash, latest_version, published_version, status, created_by, owner_user_id, gmt_create, gmt_modified`;

export class BenchTemplateRepository {
  constructor(private db: IDatabase) {}

  async listAll(filters?: { ownerUserId?: string; domainId?: string; status?: string; includeArchived?: boolean }): Promise<BenchTemplateRow[]> {
    const conditions: string[] = [];
    const values: unknown[] = [];

    if (filters?.ownerUserId) {
      conditions.push("owner_user_id = ?");
      values.push(filters.ownerUserId);
    }
    if (filters?.domainId) {
      conditions.push("domain_id = ?");
      values.push(filters.domainId);
    }
    if (filters?.status) {
      conditions.push("status = ?");
      values.push(filters.status);
    } else if (!filters?.includeArchived) {
      conditions.push("status <> 'archived'");
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const sql = `SELECT ${SELECT_COLUMNS} FROM cm_bench_templates ${where} ORDER BY domain_id, template_name`;
    return this.db.query<BenchTemplateRow>(sql, values);
  }

  async findByOwnerDomainAndName(ownerUserId: string, domainId: string, templateName: string): Promise<BenchTemplateRow | null> {
    const rows = await this.db.query<BenchTemplateRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_templates WHERE owner_user_id = ? AND domain_id = ? AND template_name = ?`,
      [ownerUserId, domainId, templateName],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateBenchTemplateInput): Promise<BenchTemplateRow> {
    const now = this.db.dialect.now();
    const ownerUserId = input.ownerUserId ?? input.createdBy ?? "";
    await this.db.exec(
      `INSERT INTO cm_bench_templates (domain_id, template_name, display_name, description, category, target_type, grading_type, source, source_path, source_hash, latest_version, status, created_by, owner_user_id, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.domainId,
        input.templateName,
        input.displayName ?? null,
        input.description ?? null,
        input.category ?? null,
        input.targetType ?? "agent_session",
        input.gradingType ?? "automated",
        input.source ?? "agentbench",
        input.sourcePath ?? null,
        input.sourceHash ?? null,
        input.latestVersion ?? 1,
        input.status ?? "draft",
        input.createdBy ?? null,
        ownerUserId,
        now,
        now,
      ],
    );
    const result = await this.findByOwnerDomainAndName(ownerUserId, input.domainId, input.templateName);
    return result!;
  }

  async update(ownerUserId: string, domainId: string, templateName: string, input: UpdateBenchTemplateInput): Promise<BenchTemplateRow | null> {
    const existing = await this.findByOwnerDomainAndName(ownerUserId, domainId, templateName);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ["display_name", input.displayName],
      ["description", input.description],
      ["category", input.category],
      ["target_type", input.targetType],
      ["grading_type", input.gradingType],
      ["source_path", input.sourcePath],
      ["source_hash", input.sourceHash],
      ["latest_version", input.latestVersion],
      ["published_version", input.publishedVersion],
      ["status", input.status],
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
    values.push(templateName);

    await this.db.exec(
      `UPDATE cm_bench_templates SET ${sets.join(", ")} WHERE owner_user_id = ? AND domain_id = ? AND template_name = ?`,
      values,
    );
    return this.findByOwnerDomainAndName(ownerUserId, domainId, templateName);
  }

  async upsert(input: CreateBenchTemplateInput): Promise<BenchTemplateRow> {
    const ownerUserId = input.ownerUserId ?? input.createdBy ?? "";
    const existing = await this.findByOwnerDomainAndName(ownerUserId, input.domainId, input.templateName);
    if (existing) {
      await this.update(ownerUserId, input.domainId, input.templateName, {
        displayName: input.displayName,
        description: input.description,
        category: input.category,
        targetType: input.targetType,
        gradingType: input.gradingType,
        sourcePath: input.sourcePath,
        sourceHash: input.sourceHash,
        latestVersion: input.latestVersion,
        status: input.status,
      });
      const updated = await this.findByOwnerDomainAndName(ownerUserId, input.domainId, input.templateName);
      if (!updated) throw new Error("Template not found after upsert update");
      return updated;
    }
    return this.create(input);
  }

  async deleteByOwnerDomainAndName(ownerUserId: string, domainId: string, templateName: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM cm_bench_templates WHERE owner_user_id = ? AND domain_id = ? AND template_name = ?",
      [ownerUserId, domainId, templateName],
    );
    return result.affectedRows > 0;
  }

  async countByOwnerAndDomain(ownerUserId: string, domainId: string, filters?: { includeArchived?: boolean }): Promise<number> {
    const archivedClause = filters?.includeArchived ? "" : " AND status <> 'archived'";
    const rows = await this.db.query<{ cnt: number }>(
      `SELECT COUNT(*) as cnt FROM cm_bench_templates WHERE owner_user_id = ? AND domain_id = ?${archivedClause}`,
      [ownerUserId, domainId],
    );
    return rows[0]?.cnt ?? 0;
  }
}
