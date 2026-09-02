/**
 * NodeStepTraceRepository — reads and writes node_step_traces table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "../db.js";

export type NodeStepTraceRow = {
  id: number;
  flow_id: string;
  node_id: string;
  attempt: number;
  step_seq: number;
  step_type: string; // 'tool_call' | 'tool_result' | 'assistant_text' | 'progress'
  skill_name: string | null;
  tool_name: string | null;
  tool_use_id: string | null;
  tool_input_json: string | null;
  tool_output_text: string | null;
  is_error: number;
  text_content: string | null;
  session_key: string | null;
  trace_id: string | null;
  observation_id: string | null;
  model: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  gmt_create: number;
};

export type NodeStepTraceInsert = {
  flowId: string;
  nodeId: string;
  attempt: number;
  stepSeq: number;
  stepType: string;
  skillName?: string | null;
  toolName?: string | null;
  toolUseId?: string | null;
  toolInputJson?: string | null;
  toolOutputText?: string | null;
  isError?: number;
  textContent?: string | null;
  sessionKey?: string | null;
  traceId?: string | null;
  observationId?: string | null;
  modelVal?: string | null;
  latencyMs?: number | null;
  promptTokens?: number | null;
  completionTokens?: number | null;
};

export type NodeStepTraceSummary = {
  node_id: string;
  attempt: number;
  skill_name: string | null;
  total_steps: number;
  tool_call_count: number;
  tool_error_count: number;
};

export class NodeStepTraceRepository {
  constructor(private db: IDatabase) {}

  async insertBatch(steps: NodeStepTraceInsert[]): Promise<number> {
    if (steps.length === 0) return 0;

    const now = this.db.dialect.now();

    // Build a single multi-row INSERT to avoid OceanBase "maximum open cursors exceeded"
    // when using prepared statements (pool.execute) in a loop.
    // INSERT INTO ... VALUES (?,?,...), (?,?,...), ... — one statement, one cursor.
    const COLUMNS = 21; // columns per row (original 14 + 7 new fields)
    const valuePlaceholder = `(${Array.from({ length: COLUMNS }, () => "?").join(", ")})`;
    const placeholders = steps.map(() => valuePlaceholder).join(", ");

    // Flatten all values into a single params array with VARCHAR truncation
    const params: unknown[] = [];
    for (const step of steps) {
      params.push(
        step.flowId,
        step.nodeId,
        step.attempt,
        step.stepSeq,
        step.stepType,
        step.skillName ? step.skillName.substring(0, 255) : null,
        step.toolName ? step.toolName.substring(0, 255) : null,
        step.toolUseId ? step.toolUseId.substring(0, 255) : null,
        step.toolInputJson ?? null,
        step.toolOutputText ?? null,
        step.isError ?? 0,
        step.textContent ?? null,
        step.sessionKey ? step.sessionKey.substring(0, 512) : null,
        step.traceId ?? null,
        step.observationId ?? null,
        step.modelVal ?? null,
        step.latencyMs ?? null,
        step.promptTokens ?? null,
        step.completionTokens ?? null,
        now,
        now,
      );
    }

    try {
      const result = await this.db.exec(
        `INSERT INTO node_step_traces (
          flow_id, node_id, attempt, step_seq, step_type,
          skill_name, tool_name, tool_use_id,
          tool_input_json, tool_output_text, is_error, text_content,
          session_key, trace_id, observation_id, model, latency_ms, prompt_tokens, completion_tokens,
          gmt_create, gmt_modified
        ) VALUES ${placeholders}`,
        params,
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[db] NodeStepTraceRepository.insertBatch: INSERT FAILED (batch ${steps.length} rows): ${msg}`);
      throw error;
    }
  }

  /** Insert a single step record (used for progress steps during execution). */
  async insert(step: NodeStepTraceInsert): Promise<number> {
    const now = this.db.dialect.now();
    try {
      const result = await this.db.exec(
        `INSERT INTO node_step_traces (
          flow_id, node_id, attempt, step_seq, step_type,
          skill_name, tool_name, tool_use_id,
          tool_input_json, tool_output_text, is_error, text_content,
          session_key, trace_id, observation_id, model, latency_ms, prompt_tokens, completion_tokens,
          gmt_create, gmt_modified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          step.flowId,
          step.nodeId,
          step.attempt,
          step.stepSeq,
          step.stepType,
          step.skillName ? step.skillName.substring(0, 255) : null,
          step.toolName ? step.toolName.substring(0, 255) : null,
          step.toolUseId ? step.toolUseId.substring(0, 255) : null,
          step.toolInputJson ?? null,
          step.toolOutputText ?? null,
          step.isError ?? 0,
          step.textContent ?? null,
          step.sessionKey ? step.sessionKey.substring(0, 512) : null,
          step.traceId ?? null,
          step.observationId ?? null,
          step.modelVal ?? null,
          step.latencyMs ?? null,
          step.promptTokens ?? null,
          step.completionTokens ?? null,
          now,
          now,
        ],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[db] NodeStepTraceRepository.insert: INSERT FAILED: ${msg}`);
      return 0;
    }
  }

  async findByFlowNode(
    flowId: string,
    nodeId: string,
    attempt = 1,
    options?: { stepType?: string; limit?: number; offset?: number },
  ): Promise<NodeStepTraceRow[]> {
    try {
      const limit = options?.limit ?? 100;
      const offset = options?.offset ?? 0;

      if (options?.stepType) {
        return await this.db.query<NodeStepTraceRow>(
          `SELECT * FROM node_step_traces
           WHERE flow_id = ? AND node_id = ? AND attempt = ? AND step_type = ?
           ORDER BY step_seq ASC LIMIT ? OFFSET ?`,
          [flowId, nodeId, attempt, options.stepType, limit, offset],
        );
      }

      return await this.db.query<NodeStepTraceRow>(
        `SELECT * FROM node_step_traces
         WHERE flow_id = ? AND node_id = ? AND attempt = ?
         ORDER BY step_seq ASC LIMIT ? OFFSET ?`,
        [flowId, nodeId, attempt, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.findByFlowNode failed: ${msg}`);
      return [];
    }
  }

  async findByFlowId(
    flowId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<NodeStepTraceRow[]> {
    const limit = options?.limit ?? 500;
    const offset = options?.offset ?? 0;
    try {
      return await this.db.query<NodeStepTraceRow>(
        `SELECT * FROM node_step_traces
         WHERE flow_id = ?
         ORDER BY node_id, attempt, step_seq ASC
         LIMIT ? OFFSET ?`,
        [flowId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }

  async findBySeq(
    flowId: string,
    nodeId: string,
    attempt: number,
    stepSeq: number,
  ): Promise<NodeStepTraceRow | null> {
    try {
      const rows = await this.db.query<NodeStepTraceRow>(
        `SELECT * FROM node_step_traces
         WHERE flow_id = ? AND node_id = ? AND attempt = ? AND step_seq = ?
         LIMIT 1`,
        [flowId, nodeId, attempt, stepSeq],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.findBySeq failed: ${msg}`);
      return null;
    }
  }

  async findSummaryByFlowId(flowId: string): Promise<NodeStepTraceSummary[]> {
    try {
      return await this.db.query<NodeStepTraceSummary>(
        `SELECT
           node_id,
           attempt,
           skill_name,
           COUNT(*) AS total_steps,
           SUM(CASE WHEN step_type = 'tool_call' THEN 1 ELSE 0 END) AS tool_call_count,
           SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END) AS tool_error_count
         FROM node_step_traces
         WHERE flow_id = ?
         GROUP BY node_id, attempt`,
        [flowId],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.findSummaryByFlowId failed: ${msg}`);
      return [];
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        `DELETE FROM node_step_traces WHERE flow_id = ?`,
        [flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }
}