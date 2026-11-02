import type { IDatabase } from '../db.js';
import { assertFlowRunLinkMysqlSchema } from '../schema-flow-run-links.js';

export type FlowRunLink = {
  childFlowId: string;
  parentFlowId: string;
  parentNodeId: string;
  parentAttempt: number | null;
  childWorkflowId: string;
};

type LinkRow = {
  child_flow_id: string;
  parent_flow_id: string;
  parent_node_id: string;
  parent_attempt: number | null;
  child_workflow_id: string;
};

const columns = 'child_flow_id, parent_flow_id, parent_node_id, parent_attempt, child_workflow_id';
const validId = (value: unknown): value is string => typeof value === 'string'
  && value.length > 0 && value.length <= 255 && !/[\s\x00-\x1f\x7f/\\]/u.test(value);
const fromRow = (row: LinkRow): FlowRunLink => ({ childFlowId: row.child_flow_id, parentFlowId: row.parent_flow_id,
  parentNodeId: row.parent_node_id, parentAttempt: row.parent_attempt, childWorkflowId: row.child_workflow_id });

/** Server-side only. Callers must authenticate and authorize the relationship before insertion. */
export class FlowRunLinkRepository {
  constructor(private readonly db: IDatabase) {}

  /** Insert once; a duplicate child is an error, never a reassignment or silent success. */
  async insert(link: FlowRunLink): Promise<void> {
    if (this.db.dbType === 'noop') throw new Error('Database unavailable');
    if (![link.childFlowId, link.parentFlowId, link.parentNodeId, link.childWorkflowId].every(validId)
      || link.childFlowId === link.parentFlowId
      || !(link.parentAttempt === null || Number.isSafeInteger(link.parentAttempt) && link.parentAttempt > 0 && link.parentAttempt <= 2_147_483_647)) {
      throw new Error('Invalid flow run link');
    }
    if (this.db.dbType === 'mysql' || this.db.dbType === 'zdas') await assertFlowRunLinkMysqlSchema(this.db);
    const result = await this.db.exec(`INSERT INTO flow_run_links (${columns}) VALUES (?, ?, ?, ?, ?)`,
      [link.childFlowId, link.parentFlowId, link.parentNodeId, link.parentAttempt, link.childWorkflowId]);
    if (result.affectedRows !== 1) throw new Error('Flow run link persistence failed');
  }

  async findByChild(childFlowId: string): Promise<FlowRunLink | null> {
    const rows = await this.db.query<LinkRow>(`SELECT ${columns} FROM flow_run_links WHERE child_flow_id = ? LIMIT 1`, [childFlowId]);
    return rows[0] ? fromRow(rows[0]) : null;
  }

  async listByParent(parentFlowId: string, limit: number): Promise<FlowRunLink[]> {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) throw new Error('Invalid link limit');
    const rows = await this.db.query<LinkRow>(`SELECT ${columns} FROM flow_run_links WHERE parent_flow_id = ? ORDER BY gmt_create DESC, child_flow_id DESC LIMIT ${limit}`, [parentFlowId]);
    return rows.map(fromRow);
  }
}
