/**
 * NodeStepTraceApiRepository — HTTP client implementation of INodeStepTraceRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for node step traces.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  INodeStepTraceRepository,
  NodeStepTraceRow,
  NodeStepTraceInsert,
  NodeStepTraceSummary,
} from "../repositories/types.js";

export class NodeStepTraceApiRepository implements INodeStepTraceRepository {
  constructor(private api: ApiClient) {}

  async insertBatch(steps: NodeStepTraceInsert[]): Promise<number> {
    void steps;
    console.warn(
      "[NodeStepTraceApi] insertBatch is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async insert(step: NodeStepTraceInsert): Promise<number> {
    void step;
    console.warn(
      "[NodeStepTraceApi] insert is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async findByFlowNode(
    flowId: string, nodeId: string, attempt?: number,
  ): Promise<NodeStepTraceRow[]> {
    void flowId; void nodeId; void attempt;
    console.warn(
      "[NodeStepTraceApi] findByFlowNode is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async findBySeq(
    flowId: string, nodeId: string, attempt: number, stepSeq: number,
  ): Promise<NodeStepTraceRow | null> {
    void flowId; void nodeId; void attempt; void stepSeq;
    console.warn(
      "[NodeStepTraceApi] findBySeq is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async findSummaryByFlowId(flowId: string): Promise<NodeStepTraceSummary[]> {
    void flowId;
    console.warn(
      "[NodeStepTraceApi] findSummaryByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    void flowId;
    console.warn(
      "[NodeStepTraceApi] deleteByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }
}