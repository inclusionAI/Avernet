/**
 * FlowEventApiRepository — HTTP client implementation of IFlowEventRepository.
 *
 * Calls evolvetrace's /api/internal/events endpoints.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IFlowEventRepository,
  FlowEventRow,
  FlowEventInsert,
  FindEventOptions,
  TimeRangeOptions,
} from "../repositories/types.js";

export class FlowEventApiRepository implements IFlowEventRepository {
  constructor(private api: ApiClient) {}

  private normalizeRow(row: any): FlowEventRow {
    return {
      id: row.id ?? 0,
      event_id: row.event_id ?? "",
      flow_id: row.flow_id ?? "",
      workflow_id: row.workflow_id ?? "",
      node_id: row.node_id ?? null,
      event_type: row.event_type ?? "",
      attempt: row.attempt ?? null,
      time: row.time ?? 0,
      data_json: row.data_json ?? null,
      error_text: row.error_text ?? null,
      gmt_create: row.gmt_create ?? 0,
    };
  }

  async insert(event: FlowEventInsert): Promise<boolean> {
    try {
      const body: Record<string, unknown> = {
        event_id: event.id,
        flow_id: event.flowId,
        event_type: event.type,
      };
      if (event.workflowId) body.workflow_id = event.workflowId;
      if (event.nodeId !== undefined) body.node_id = event.nodeId;
      if (event.attempt !== undefined) body.attempt = event.attempt;
      if (event.time) body.time = event.time;
      if (event.data !== undefined) body.data_json = JSON.stringify(event.data);
      if (event.error !== undefined) body.error_text = event.error;

      const resp = await this.api.post<{ success: boolean; data: { inserted: boolean } }>("/api/internal/events", body);
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[EventApi] insert failed: ${msg}`);
      return false;
    }
  }

  async findByFlowId(flowId: string, options: FindEventOptions = {}): Promise<FlowEventRow[]> {
    try {
      const query: Record<string, string> = { flowId };
      if (options.limit) query.limit = String(options.limit);
      if (options.offset) query.offset = String(options.offset);

      const resp = await this.api.get<{ success: boolean; data: any[]; total?: number }>("/api/internal/events", query);
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      return rows.map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[EventApi] findByFlowId failed: ${msg}`);
      return [];
    }
  }

  async findByWorkflowAndTimeRange(
    workflowId: string,
    startTime: number,
    endTime: number,
    options: TimeRangeOptions = {},
  ): Promise<FlowEventRow[]> {
    try {
      // The API doesn't have a direct endpoint for time-range queries by workflow,
      // so we fetch by flow IDs first or query the events endpoint.
      // For now, fetch all events for the workflow and filter client-side.
      const query: Record<string, string> = { flowId: workflowId };
      if (options.limit) query.limit = String(options.limit);
      if (options.offset) query.offset = String(options.offset);

      const resp = await this.api.get<{ success: boolean; data: any[] }>("/api/internal/events", query);
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      return rows
        .filter((r: any) => r.workflow_id === workflowId && r.time >= startTime && r.time <= endTime)
        .map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[EventApi] findByWorkflowAndTimeRange failed: ${msg}`);
      return [];
    }
  }
}