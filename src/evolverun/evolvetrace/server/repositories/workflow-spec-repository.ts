/**
 * Repository for workflow_specs table — browser-persisted workflow edits.
 * ClawWeb's own table, not from ClawFlow.
 */
import type { IDatabase } from "../db.js";
import { parse as parseYaml } from "yaml";
import { normalizeWorkflowSpec } from "../workflow.js";

export type WorkflowSpecRow = {
  id: number;
  workflow_id: string;
  pack_id: string | null;
  spec_json: string;
  gmt_create: number | string;
  gmt_modified: number | string;
  title: string;
  version: number | null;
};

/** Lightweight summary row — avoids loading spec_json (MEDIUMTEXT) for list views. */
export type WorkflowSpecSummary = {
  workflow_id: string;
  pack_id: string | null;
  title: string | null;
  gmt_modified: number | string;
};

/**
 * Extract title from spec_json without requiring the caller to parse JSON.
 * Handles both direct JSON format and {"content": "yaml-string"} wrapper format.
 */
export function extractTitleFromSpecJson(specJson: string, fallbackId: string): string {
  try {
    const parsed = JSON.parse(specJson) as Record<string, unknown>;
    if (typeof parsed.content === "string" && !Array.isArray(parsed.nodes)) {
      try {
        const raw = parseYaml(parsed.content) as unknown;
        const spec = normalizeWorkflowSpec(raw);
        if (spec.title) return spec.title;
      } catch { /* use fallback */ }
    } else if (parsed.title) {
      return parsed.title as string;
    }
  } catch { /* use fallback */ }
  return fallbackId;
}

export class WorkflowSpecRepository {
  constructor(private db: IDatabase) {}

  /** List all workflow specs with full spec_json (use only when spec_json is needed). */
  async listAll(): Promise<WorkflowSpecRow[]> {
    return this.db.query<WorkflowSpecRow>(
      "SELECT id, workflow_id, pack_id, spec_json, gmt_create, gmt_modified, title FROM workflow_specs ORDER BY gmt_modified DESC",
    );
  }

  /** List workflow summaries without loading spec_json — used for list/table views. */
  async listSummaries(): Promise<WorkflowSpecSummary[]> {
    return this.db.query<WorkflowSpecSummary>(
      "SELECT workflow_id, pack_id, title, gmt_modified FROM workflow_specs ORDER BY gmt_modified DESC",
    );
  }

  async findByWorkflowId(workflowId: string): Promise<WorkflowSpecRow | null> {
    const rows = await this.db.query<WorkflowSpecRow>(
      "SELECT id, workflow_id, pack_id, spec_json, gmt_create, gmt_modified, title FROM workflow_specs WHERE workflow_id = ?",
      [workflowId],
    );
    return rows[0] ?? null;
  }

  async upsert(workflowId: string, packId: string | null, specJson: string): Promise<WorkflowSpecRow> {
    const now = this.db.dialect.now();
    const existing = await this.findByWorkflowId(workflowId);
    const title = extractTitleFromSpecJson(specJson, workflowId);

    if (existing) {
      await this.db.exec(
        "UPDATE workflow_specs SET spec_json = ?, pack_id = ?, title = ?, gmt_modified = ? WHERE workflow_id = ?",
        [specJson, packId, title, now, workflowId],
      );
      return { ...existing, spec_json: specJson, pack_id: packId, title, gmt_modified: now as number };
    }

    await this.db.exec(
      "INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, ?)",
      [workflowId, packId, specJson, title, now, now],
    );
    const result = await this.findByWorkflowId(workflowId);
    return result!;
  }

  /** Check if a workflow with the given ID already exists */
  async existsByWorkflowId(workflowId: string): Promise<boolean> {
    const rows = await this.db.query<{ cnt: number }>(
      "SELECT COUNT(*) as cnt FROM workflow_specs WHERE workflow_id = ?",
      [workflowId],
    );
    return rows[0].cnt > 0;
  }

  /** Update a workflow by its original ID (for workflowId rename), including changing the workflow_id column */
  async updateByOriginalId(originalId: string, newId: string, packId: string | null, specJson: string): Promise<WorkflowSpecRow | null> {
    const now = this.db.dialect.now();
    const title = extractTitleFromSpecJson(specJson, newId);
    await this.db.exec(
      "UPDATE workflow_specs SET workflow_id = ?, spec_json = ?, pack_id = ?, title = ?, gmt_modified = ? WHERE workflow_id = ?",
      [newId, specJson, packId, title, now, originalId],
    );
    return this.findByWorkflowId(newId);
  }

  async delete(workflowId: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM workflow_specs WHERE workflow_id = ?",
      [workflowId],
    );
    return result.affectedRows > 0;
  }

  async findPage(opts: { page: number; pageSize: number; search?: string }): Promise<{ rows: WorkflowSpecSummary[]; total: number }> {
    const offset = (opts.page - 1) * opts.pageSize;
    const limit = opts.pageSize;

    let whereClause = '';
    const params: unknown[] = [];

    if (opts.search) {
      whereClause = 'WHERE workflow_id LIKE ? OR title LIKE ?';
      const pattern = `%${opts.search}%`;
      params.push(pattern, pattern);
    }

    const countRows = await this.db.query<{ cnt: number }>(
      `SELECT COUNT(*) as cnt FROM workflow_specs ${whereClause}`,
      params,
    );
    const total = countRows[0]?.cnt ?? 0;

    const rows = await this.db.query<WorkflowSpecSummary>(
      `SELECT workflow_id, pack_id, title, gmt_modified FROM workflow_specs ${whereClause} ORDER BY gmt_modified DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );

    return { rows, total };
  }

  /**
   * Backfill the title column for existing rows where title is NULL.
   * Called once after migration v32.
   */
  async backfillTitles(): Promise<number> {
    const rows = await this.db.query<{ workflow_id: string; spec_json: string }>(
      "SELECT workflow_id, spec_json FROM workflow_specs WHERE title IS NULL",
    );
    let count = 0;
    for (const row of rows) {
      const title = extractTitleFromSpecJson(row.spec_json, row.workflow_id);
      await this.db.exec(
        "UPDATE workflow_specs SET title = ? WHERE workflow_id = ?",
        [title, row.workflow_id],
      );
      count++;
    }
    return count;
  }

  /** Update the version column for a workflow (called on deploy success). */
  async updateVersion(workflowId: string, version: number): Promise<void> {
    await this.db.exec(
      "UPDATE workflow_specs SET version = ?, gmt_modified = ? WHERE workflow_id = ?",
      [version, this.db.dialect.now(), workflowId],
    );
  }
}