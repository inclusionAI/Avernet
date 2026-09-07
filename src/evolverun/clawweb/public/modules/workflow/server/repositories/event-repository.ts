/**
 * FlowEventRepository — reads and writes flow_events table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type FlowEventRow = {
  id: number;
  event_id: string;
  flow_id: string;
  workflow_id: string;
  node_id: string | null;
  event_type: string;
  attempt: number | null;
  time: number;
  data_json: string | null;
  error_text: string | null;
  gmt_create: number;
};

export type FlowEventInsert = {
  id: string;
  time: number;
  type: string;
  flowId: string;
  workflowId: string;
  nodeId?: string | null;
  actionId?: string | null;
  attempt?: number;
  data?: Record<string, unknown>;
  error?: string | null;
};

export type FindFlowEventsOptions = {
  limit?: number;
  offset?: number;
};

export type FindFlowEventsByWorkflowOptions = {
  limit?: number;
  offset?: number;
};

export class FlowEventRepository {
  constructor(private db: IDatabase) {}

  // ── Read methods ──

  async findByFlowId(flowId: string, options: FindFlowEventsOptions = {}): Promise<FlowEventRow[]> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    try {
      return await this.db.query<FlowEventRow>(
        "SELECT * FROM flow_events WHERE flow_id = ? ORDER BY time DESC LIMIT ? OFFSET ?",
        [flowId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowEventRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }

  async findByWorkflowAndTimeRange(
    workflowId: string,
    startTime: number,
    endTime: number,
    options: FindFlowEventsByWorkflowOptions = {},
  ): Promise<FlowEventRow[]> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    try {
      return await this.db.query<FlowEventRow>(
        "SELECT * FROM flow_events WHERE workflow_id = ? AND time >= ? AND time <= ? ORDER BY time DESC LIMIT ? OFFSET ?",
        [workflowId, startTime, endTime, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowEventRepository.findByWorkflowAndTimeRange failed: ${msg}`);
      return [];
    }
  }

  // ── Write methods (best-effort: catch errors, log, return false) ──

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM flow_events WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowEventRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async insert(event: FlowEventInsert): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      await this.db.exec(
        `INSERT INTO flow_events (event_id, flow_id, workflow_id, node_id, event_type, attempt, time, data_json, error_text, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          event.id,
          event.flowId,
          event.workflowId,
          event.nodeId ?? null,
          event.type,
          event.attempt ?? null,
          event.time,
          event.data ? JSON.stringify(event.data) : null,
          event.error ?? null,
          now,
          now,
        ],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowEventRepository.insert failed: ${msg}`);
      return false;
    }
  }
}