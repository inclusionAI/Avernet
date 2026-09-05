/**
 * Repository for cm_bench_template_versions table — immutable/draft version content.
 */
import type { IDatabase } from "../db.js";

export type BenchTemplateVersionRow = {
  id: number;
  domain_id: string;
  template_name: string;
  version: number;
  display_name: string | null;
  description: string | null;
  content_md: string;
  parsed_meta_json: string | null;
  source_path: string | null;
  source_hash: string | null;
  status: string;
  created_by: string | null;
  owner_user_id: string;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateBenchTemplateVersionInput = {
  domainId: string;
  templateName: string;
  version: number;
  displayName?: string | null;
  description?: string | null;
  contentMd: string;
  parsedMetaJson?: string | null;
  sourcePath?: string | null;
  sourceHash?: string | null;
  status?: string;
  createdBy?: string | null;
  ownerUserId?: string;
};

export type UpdateBenchTemplateVersionInput = {
  displayName?: string | null;
  description?: string | null;
  contentMd?: string;
  parsedMetaJson?: string | null;
  sourcePath?: string | null;
  sourceHash?: string | null;
  status?: string;
};

const SELECT_COLUMNS = `id, domain_id, template_name, version, display_name, description, content_md, parsed_meta_json, source_path, source_hash, status, created_by, owner_user_id, gmt_create, gmt_modified`;

export class BenchTemplateVersionRepository {
  constructor(private db: IDatabase) {}

  async listByOwnerDomainAndName(ownerUserId: string, domainId: string, templateName: string): Promise<BenchTemplateVersionRow[]> {
    return this.db.query<BenchTemplateVersionRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_template_versions WHERE owner_user_id = ? AND domain_id = ? AND template_name = ? ORDER BY version DESC`,
      [ownerUserId, domainId, templateName],
    );
  }

  async findByOwnerDomainNameVersion(ownerUserId: string, domainId: string, templateName: string, version: number): Promise<BenchTemplateVersionRow | null> {
    const rows = await this.db.query<BenchTemplateVersionRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_template_versions WHERE owner_user_id = ? AND domain_id = ? AND template_name = ? AND version = ?`,
      [ownerUserId, domainId, templateName, version],
    );
    return rows[0] ?? null;
  }

  async findLatestVersionByOwner(ownerUserId: string, domainId: string, templateName: string): Promise<number> {
    const rows = await this.db.query<{ max_v: number | null }>(
      `SELECT MAX(version) as max_v FROM cm_bench_template_versions WHERE owner_user_id = ? AND domain_id = ? AND template_name = ?`,
      [ownerUserId, domainId, templateName],
    );
    return rows[0]?.max_v ?? 0;
  }

  async findDraftVersionByOwner(ownerUserId: string, domainId: string, templateName: string): Promise<BenchTemplateVersionRow | null> {
    const rows = await this.db.query<BenchTemplateVersionRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_template_versions WHERE owner_user_id = ? AND domain_id = ? AND template_name = ? AND status = 'draft' ORDER BY version DESC LIMIT 1`,
      [ownerUserId, domainId, templateName],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateBenchTemplateVersionInput): Promise<BenchTemplateVersionRow> {
    const now = this.db.dialect.now();
    const ownerUserId = input.ownerUserId ?? input.createdBy ?? "";
    await this.db.exec(
      `INSERT INTO cm_bench_template_versions (domain_id, template_name, version, display_name, description, content_md, parsed_meta_json, source_path, source_hash, status, created_by, owner_user_id, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.domainId,
        input.templateName,
        input.version,
        input.displayName ?? null,
        input.description ?? null,
        input.contentMd,
        input.parsedMetaJson ?? null,
        input.sourcePath ?? null,
        input.sourceHash ?? null,
        input.status ?? "draft",
        input.createdBy ?? null,
        ownerUserId,
        now,
        now,
      ],
    );
    const created = await this.findByOwnerDomainNameVersion(ownerUserId, input.domainId, input.templateName, input.version);
    if (!created) throw new Error("Version not found after create");
    return created;
  }

  async update(ownerUserId: string, domainId: string, templateName: string, version: number, input: UpdateBenchTemplateVersionInput): Promise<BenchTemplateVersionRow | null> {
    const existing = await this.findByOwnerDomainNameVersion(ownerUserId, domainId, templateName, version);
    if (!existing) return null;
    if (existing.status === "published") {
      throw new Error("Cannot modify a published version");
    }

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ["display_name", input.displayName],
      ["description", input.description],
      ["content_md", input.contentMd],
      ["parsed_meta_json", input.parsedMetaJson],
      ["source_path", input.sourcePath],
      ["source_hash", input.sourceHash],
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
    values.push(version);

    await this.db.exec(
      `UPDATE cm_bench_template_versions SET ${sets.join(", ")} WHERE owner_user_id = ? AND domain_id = ? AND template_name = ? AND version = ?`,
      values,
    );
    return this.findByOwnerDomainNameVersion(ownerUserId, domainId, templateName, version);
  }

  async publishByOwner(ownerUserId: string, domainId: string, templateName: string, version: number): Promise<BenchTemplateVersionRow | null> {
    const existing = await this.findByOwnerDomainNameVersion(ownerUserId, domainId, templateName, version);
    if (!existing) return null;

    const now = this.db.dialect.now();
    await this.db.exec(
      `UPDATE cm_bench_template_versions SET status = 'published', gmt_modified = ? WHERE owner_user_id = ? AND domain_id = ? AND template_name = ? AND version = ?`,
      [now, ownerUserId, domainId, templateName, version],
    );
    return this.findByOwnerDomainNameVersion(ownerUserId, domainId, templateName, version);
  }

  async deleteByOwnerDomainAndName(ownerUserId: string, domainId: string, templateName: string): Promise<number> {
    const result = await this.db.exec(
      "DELETE FROM cm_bench_template_versions WHERE owner_user_id = ? AND domain_id = ? AND template_name = ?",
      [ownerUserId, domainId, templateName],
    );
    return result.affectedRows;
  }
}
