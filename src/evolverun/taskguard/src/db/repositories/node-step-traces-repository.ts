/**
 * NodeStepTraceRepository — persists and queries node_step_traces records.
 *
 * Stores structured step data from embedded-agent node executions:
 * - tool_call: agent invoked a tool with input parameters
 * - tool_result: tool returned output (or error)
 * - assistant_text: agent produced text output
 * - progress: user-facing progress message (e.g., "调用 xxx 技能", "工具调用完成：...")
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type {
  INodeStepTraceRepository,
  NodeStepTraceRow,
  NodeStepTraceInsert,
  NodeStepTraceSummary,
} from "./types.js";

const STEP_INSERT_SQL = `INSERT INTO node_step_traces (
  flow_id, node_id, attempt, step_seq, step_type,
  skill_name, tool_name, tool_use_id,
  tool_input_json, tool_output_text, is_error, text_content,
  session_key, trace_id, observation_id, model, latency_ms, prompt_tokens, completion_tokens,
  gmt_create, gmt_modified
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`;

function stepToParams(step: NodeStepTraceInsert, now: number | string): unknown[] {
  return [
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
    step.model ?? null,
    step.latencyMs ?? null,
    step.promptTokens ?? null,
    step.completionTokens ?? null,
    now,
    now,
  ];
}

export class NodeStepTraceRepository implements INodeStepTraceRepository {
  constructor(private db: IDatabase) {}

  /**
   * Batch insert step records for a node execution.
   * Returns the number of rows inserted.
   */
  async insertBatch(steps: NodeStepTraceInsert[]): Promise<number> {
    if (steps.length === 0) return 0;

    try {
      // Single multi-row INSERT to avoid "maximum open cursors exceeded" on OceanBase
      // when using prepared statements in a loop. N rows = 1 cursor instead of N.
      const COLUMNS = 21;
      const valuePlaceholder = `(${Array.from({ length: COLUMNS }, () => "?").join(", ")})`;
      const placeholders = steps.map(() => valuePlaceholder).join(", ");

      const now = nowForDb(this.db.dbType);
      const params: unknown[] = [];
      for (const step of steps) {
        params.push(...stepToParams(step, now));
      }

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
      console.warn(`[db] NodeStepTraceRepository.insertBatch failed: ${msg}`);
      return 0;
    }
  }

  /** Insert a single step record (used for progress steps during execution). */
  async insert(step: NodeStepTraceInsert): Promise<number> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(STEP_INSERT_SQL, stepToParams(step, now));
      return 1;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.insert failed: ${msg}`);
      return 0;
    }
  }

  /** Find all steps for a node execution, ordered by step_seq. */
  async findByFlowNode(
    flowId: string,
    nodeId: string,
    attempt = 1,
  ): Promise<NodeStepTraceRow[]> {
    try {
      return await this.db.query<NodeStepTraceRow>(
        `SELECT * FROM node_step_traces
         WHERE flow_id = ? AND node_id = ? AND attempt = ?
         ORDER BY step_seq ASC`,
        [flowId, nodeId, attempt],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.findByFlowNode failed: ${msg}`);
      return [];
    }
  }

  /** Find a single step by sequence number. */
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

  /** Get step summary stats for all nodes in a flow run. */
  async findSummaryByFlowId(flowId: string): Promise<NodeStepTraceSummary[]> {
    try {
      const rows = await this.db.query<Row>(
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

      return rows.map((r) => ({
        nodeId: r.node_id as string,
        attempt: r.attempt as number,
        skillName: r.skill_name as string | null,
        toolCallCount: r.tool_call_count as number,
        toolErrorCount: r.tool_error_count as number,
        totalSteps: r.total_steps as number,
      }));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] NodeStepTraceRepository.findSummaryByFlowId failed: ${msg}`);
      return [];
    }
  }

  /** Delete steps for a flow run (cleanup). */
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