/**
 * Repository for knowledge_bases table — GRT knowledge base configurations.
 */
import type { IDatabase } from "../db.js";

export type KnowledgeBaseRow = {
  id: number;
  kb_id: string;
  name: string;
  description: string | null;
  instance_name: string;
  interface_name: string;
  token: string;
  user_name: string;
  user_id: string;
  top_k: number;
  ranking_threshold: number;
  vector_threshold: number;
  ranking_model: string;
  env: string;
  enabled: number;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateKnowledgeBaseInput = {
  kb_id: string;
  name: string;
  description?: string;
  instance_name: string;
  interface_name: string;
  token: string;
  user_name?: string;
  user_id?: string;
  top_k?: number;
  ranking_threshold?: number;
  vector_threshold?: number;
  ranking_model?: string;
  env?: string;
};

export type UpdateKnowledgeBaseInput = {
  name?: string;
  description?: string;
  instance_name?: string;
  interface_name?: string;
  token?: string;
  user_name?: string;
  user_id?: string;
  top_k?: number;
  ranking_threshold?: number;
  vector_threshold?: number;
  ranking_model?: string;
  env?: string;
  enabled?: number;
};

const SELECT_COLUMNS = `id, kb_id, name, description, instance_name, interface_name, token, user_name, user_id, top_k, ranking_threshold, vector_threshold, ranking_model, env, enabled, gmt_create, gmt_modified`;

export class KnowledgeBaseRepository {
  constructor(private db: IDatabase) {}

  async listAll(enabledOnly = false): Promise<KnowledgeBaseRow[]> {
    const sql = enabledOnly
      ? `SELECT ${SELECT_COLUMNS} FROM knowledge_bases WHERE enabled = 1 ORDER BY name`
      : `SELECT ${SELECT_COLUMNS} FROM knowledge_bases ORDER BY name`;
    return this.db.query<KnowledgeBaseRow>(sql);
  }

  async findByKbId(kbId: string): Promise<KnowledgeBaseRow | null> {
    const rows = await this.db.query<KnowledgeBaseRow>(
      `SELECT ${SELECT_COLUMNS} FROM knowledge_bases WHERE kb_id = ?`,
      [kbId],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateKnowledgeBaseInput): Promise<KnowledgeBaseRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO knowledge_bases (kb_id, name, description, instance_name, interface_name, token, user_name, user_id, top_k, ranking_threshold, vector_threshold, ranking_model, env, enabled, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
      [
        input.kb_id,
        input.name,
        input.description ?? null,
        input.instance_name,
        input.interface_name,
        input.token,
        input.user_name ?? '',
        input.user_id ?? '',
        input.top_k ?? 3,
        input.ranking_threshold ?? 0.01,
        input.vector_threshold ?? 0.6,
        input.ranking_model ?? 'bge-reranker-base',
        input.env ?? 'prod',
        now,
        now,
      ],
    );
    const result = await this.findByKbId(input.kb_id);
    return result!;
  }

  async update(kbId: string, input: UpdateKnowledgeBaseInput): Promise<KnowledgeBaseRow | null> {
    const existing = await this.findByKbId(kbId);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ['name', input.name],
      ['description', input.description],
      ['instance_name', input.instance_name],
      ['interface_name', input.interface_name],
      ['token', input.token],
      ['user_name', input.user_name],
      ['user_id', input.user_id],
      ['top_k', input.top_k],
      ['ranking_threshold', input.ranking_threshold],
      ['vector_threshold', input.vector_threshold],
      ['ranking_model', input.ranking_model],
      ['env', input.env],
      ['enabled', input.enabled],
    ];

    for (const [col, val] of fields) {
      if (val !== undefined) {
        sets.push(`${col} = ?`);
        values.push(val);
      }
    }

    if (sets.length === 0) return existing;

    sets.push('gmt_modified = ?');
    values.push(now);
    values.push(kbId);

    await this.db.exec(
      `UPDATE knowledge_bases SET ${sets.join(', ')} WHERE kb_id = ?`,
      values,
    );
    return this.findByKbId(kbId);
  }

  async delete(kbId: string): Promise<boolean> {
    const result = await this.db.exec(
      'DELETE FROM knowledge_bases WHERE kb_id = ?',
      [kbId],
    );
    return result.affectedRows > 0;
  }
}