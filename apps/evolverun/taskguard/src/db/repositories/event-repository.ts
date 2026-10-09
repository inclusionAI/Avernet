/**
 * FlowEventRepository — persists and queries FlowEvent records.
 *
 * Implements dual-write: DB + existing JSONL log.
 * DB failure does not block JSONL writing — the JSONL log is the source of truth.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type { IFlowEventRepository } from "./types.js";

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

export type FindOptions = {
  limit?: number;
  offset?: number;
};

export type TimeRangeOptions = {
  eventType?: string;
  limit?: number;
  offset?: number;
};

export class FlowEventRepository implements IFlowEventRepository {
  constructor(private db: IDatabase) {}

  /**
   * Insert a flow event into the database.
   * Returns true on success, false on failure (never throws).
   */
  async insert(event: FlowEventInsert): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
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
          Math.floor(event.time / 1000),
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

  /** Find events by flow ID, ordered by time descending. */
  async findByFlowId(flowId: string, options: FindOptions = {}): Promise<FlowEventRow[]> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    try {
      return await this.db.query<FlowEventRow>(
        `SELECT * FROM flow_events WHERE flow_id = ? ORDER BY time DESC LIMIT ? OFFSET ?`,
        [flowId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowEventRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }

  /** Find events by workflow ID and time range. */
  async findByWorkflowAndTimeRange(
    workflowId: string,
    startTime: number,
    endTime: number,
    options: TimeRangeOptions = {},
  ): Promise<FlowEventRow[]> {
    const limit = options.limit ?? 100;
    const offset = options.offset ?? 0;
    try {
      if (options.eventType) {
        return await this.db.query<FlowEventRow>(
          `SELECT * FROM flow_events
           WHERE workflow_id = ? AND time >= ? AND time <= ? AND event_type = ?
           ORDER BY time DESC LIMIT ? OFFSET ?`,
          [workflowId, startTime, endTime, options.eventType, limit, offset],
        );
      }
      return await this.db.query<FlowEventRow>(
        `SELECT * FROM flow_events
         WHERE workflow_id = ? AND time >= ? AND time <= ?
         ORDER BY time DESC LIMIT ? OFFSET ?`,
        [workflowId, startTime, endTime, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] FlowEventRepository.findByWorkflowAndTimeRange failed: ${msg}`);
      return [];
    }
  }
}