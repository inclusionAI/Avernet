/**
 * HallucinationCheckApiRepository — HTTP client implementation of IHallucinationCheckRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for hallucination checks.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IHallucinationCheckRepository,
  HallucinationCheckRow,
  HallucinationCheckInsert,
  HallucinationCheckSummary,
} from "../repositories/types.js";

export class HallucinationCheckApiRepository implements IHallucinationCheckRepository {
  constructor(private api: ApiClient) {}

  async insertChecks(checks: HallucinationCheckInsert[]): Promise<number> {
    void checks;
    console.warn(
      "[HallucinationCheckApi] insertChecks is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async findByFlowNode(
    flowId: string, nodeId: string, attempt?: number,
  ): Promise<HallucinationCheckRow[]> {
    void flowId; void nodeId; void attempt;
    console.warn(
      "[HallucinationCheckApi] findByFlowNode is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async findSummaryByFlowId(flowId: string): Promise<HallucinationCheckSummary[]> {
    void flowId;
    console.warn(
      "[HallucinationCheckApi] findSummaryByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    void flowId;
    console.warn(
      "[HallucinationCheckApi] deleteByFlowId is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }
}