/**
 * DeployHistoryApiRepository — HTTP client implementation for deploy history operations.
 *
 * Reads from GET endpoints under /api/workflows/:workflowId/history (no signing required,
 * these are external web API endpoints).
 * Writes to POST /api/internal/deploy-history (Ed25519 signed when privateKeyB64 is set).
 *
 * When ApiClient has no privateKeyB64, write requests are sent unsigned;
 * the server's signatureMiddleware is a no-op when publicKeyB64 is empty.
 */
import type { ApiClient } from "../api-client.js";
import type { IDeployHistoryRepository } from "../repositories/types.js";

// ── API Request/Response types ──

type DeployHistoryInsertBody = {
  packId: string;
  workflowId: string;
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number;
  specJson: string;
  note?: string;
  botId?: string | null;
  ownerId?: string | null;
};

type DeployHistoryListRow = {
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number | null;
  note?: string | null;
  botId?: string | null;
  ownerId?: string | null;
  gmtCreate: number;
};

type DeployHistoryDetailRow = DeployHistoryListRow & {
  specJson: string;
};

type DeployHistoryLatestDeploy = {
  packId: string;
  workflowId: string;
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number;
};

// ── Repository ──

/**
 * DeployHistoryApiRepository provides access to the deploy_history table
 * via evolvetrace's HTTP API.
 *
 * Read operations use /api/workflows/:workflowId/history (public API, no signing).
 * Write operations use POST /api/internal/deploy-history (internal API, signed if key configured).
 */
export class DeployHistoryApiRepository implements IDeployHistoryRepository {
  constructor(private api: ApiClient) {}

  /**
   * Insert a deploy history record.
   * Called after deployToClawWeb({ skipDeployHistory: true }) — the save API creates
   * the workflow_spec but skips deploy_history, so the deploy command must write it.
   */
  async insert(input: DeployHistoryInsertBody): Promise<boolean> {
    try {
      const resp = await this.api.post("/api/internal/deploy-history", input);
      if (!resp.ok) {
        const err = resp.error ?? `HTTP ${resp.status}`;
        // 409 means version already recorded — not a hard failure for deploy success
        if (resp.status === 409) {
          console.warn(`[deploy-history] insert 409 for ${input.workflowId} v${input.version}: ${err}`);
          return true; // treat as success — record exists
        }
        throw new Error(`deploy-history insert failed: ${err}`);
      }
      return true;
    } catch (err) {
      console.warn(`[deploy-history] insert failed for ${input.workflowId} v${input.version}: ${err instanceof Error ? err.message : err}`);
      return false;
    }
  }

  /**
   * Get the latest deploy record for a workflow.
   * Returns null if no deploy records exist.
   */
  async getLatestDeploy(packId: string, workflowId: string): Promise<DeployHistoryLatestDeploy | null> {
    try {
      const resp = await this.api.get<DeployHistoryListRow[]>(`/api/workflows/${encodeURIComponent(workflowId)}/history`, { limit: "1" });
      if (!resp.ok || !resp.data) return null;
      const rows = resp.data as unknown as DeployHistoryListRow[];
      if (rows.length === 0) return null;
      const row = rows[0];
      return {
        packId,
        workflowId: row.fromDeployNumber ? workflowId : workflowId,
        deployNumber: row.deployNumber,
        version: row.version,
        tagName: row.tagName ?? "",
        action: row.action,
        fromDeployNumber: row.fromDeployNumber ?? undefined,
      };
    } catch {
      return null;
    }
  }

  /**
   * Get the latest version number from deploy history.
   * Returns 0 if no records exist.
   */
  async getLatestVersion(packId: string, workflowId: string): Promise<number> {
    try {
      const latest = await this.getLatestDeploy(packId, workflowId);
      return latest?.version ?? 0;
    } catch {
      return 0;
    }
  }

  /**
   * Get the maximum deploy_number from deploy history.
   * Returns 0 if no records exist.
   */
  async getMaxDeployNumber(packId: string, workflowId: string): Promise<number> {
    try {
      const resp = await this.api.get<DeployHistoryListRow[]>(`/api/workflows/${encodeURIComponent(workflowId)}/history`, { limit: "100" });
      if (!resp.ok || !resp.data) return 0;
      const rows = resp.data as unknown as DeployHistoryListRow[];
      if (rows.length === 0) return 0;
      return Math.max(...rows.map(r => r.deployNumber));
    } catch {
      return 0;
    }
  }

  /**
   * Find a deploy history record by version number.
   * Returns the full snapshot including specJson for rollback.
   */
  async findByVersion(packId: string, workflowId: string, version: number): Promise<DeployHistoryDetailRow | null> {
    try {
      const resp = await this.api.get<DeployHistoryDetailRow>(`/api/workflows/${encodeURIComponent(workflowId)}/history/${version}`);
      if (!resp.ok || !resp.data) return null;
      return {
        ...resp.data,
        fromDeployNumber: resp.data.fromDeployNumber ?? undefined,
      };
    } catch {
      return null;
    }
  }

  /**
   * Find a deploy history record by deploy number.
   * Returns the full snapshot including specJson for rollback.
   */
  async findByDeployNumber(packId: string, workflowId: string, deployNumber: number): Promise<DeployHistoryDetailRow | null> {
    try {
      const resp = await this.api.get<DeployHistoryDetailRow>(`/api/workflows/${encodeURIComponent(workflowId)}/history/by-deploy/${deployNumber}`);
      if (!resp.ok || !resp.data) return null;
      return {
        ...resp.data,
        fromDeployNumber: resp.data.fromDeployNumber ?? undefined,
      };
    } catch {
      return null;
    }
  }

  /**
   * List deploy history records for a workflow.
   * Returns up to `limit` records ordered by deploy_number DESC.
   */
  async listHistory(workflowId: string, limit: number = 10): Promise<DeployHistoryListRow[]> {
    try {
      const actualLimit = Math.min(100, Math.max(1, limit));
      const resp = await this.api.get<{ workflowId: string; history: DeployHistoryListRow[] }>(
        `/api/workflows/${encodeURIComponent(workflowId)}/history`,
        { limit: String(actualLimit) },
      );
      if (!resp.ok || !resp.data) return [];
      return resp.data.history ?? [];
    } catch {
      return [];
    }
  }
}