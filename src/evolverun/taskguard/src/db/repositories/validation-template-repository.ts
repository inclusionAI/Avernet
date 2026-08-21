/**
 * Repository for validation_templates table — LLM validation template configurations.
 */
import type { IDatabase } from "../types.js";
import type { IValidationTemplateRepository } from "./types.js";

export type ValidationTemplateRow = {
  id: number;
  template_id: string;
  name: string;
  description: string | null;
  content: string;
  enabled: number;
  gmt_create: number;
  gmt_modified: number;
};

export class ValidationTemplateRepository implements IValidationTemplateRepository {
  constructor(private db: IDatabase) {}

  async findByTemplateId(templateId: string): Promise<ValidationTemplateRow | null> {
    const rows = await this.db.query<ValidationTemplateRow>(
      `SELECT id, template_id, name, description, content, enabled, gmt_create, gmt_modified
       FROM validation_templates WHERE template_id = ?`,
      [templateId],
    );
    return rows[0] ?? null;
  }

  async findEnabled(templateId: string): Promise<ValidationTemplateRow | null> {
    const rows = await this.db.query<ValidationTemplateRow>(
      `SELECT id, template_id, name, description, content, enabled, gmt_create, gmt_modified
       FROM validation_templates WHERE template_id = ? AND enabled = 1`,
      [templateId],
    );
    return rows[0] ?? null;
  }

  async listAll(enabledOnly = false): Promise<ValidationTemplateRow[]> {
    const sql = enabledOnly
      ? `SELECT id, template_id, name, description, content, enabled, gmt_create, gmt_modified
         FROM validation_templates WHERE enabled = 1 ORDER BY name`
      : `SELECT id, template_id, name, description, content, enabled, gmt_create, gmt_modified
         FROM validation_templates ORDER BY name`;
    return this.db.query<ValidationTemplateRow>(sql);
  }
}