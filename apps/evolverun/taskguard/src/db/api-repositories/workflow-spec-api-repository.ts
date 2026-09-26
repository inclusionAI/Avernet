/**
 * WorkflowSpecApiRepository — HTTP client implementation of IWorkflowSpecRepository.
 *
 * Reads the saved workflow spec from evolvetrace's GET /api/workflows/:workflowId
 * (a public web API endpoint, no Ed25519 signing required).
 *
 * When ApiClient has no privateKeyB64, requests are sent unsigned; the server's
 * signatureMiddleware is a no-op when publicKeyB64 is empty.
 */
import type { ApiClient } from "../api-client.js";
import type { IWorkflowSpecRepository, WorkflowSpecRow } from "../repositories/types.js";

// ── Repository ──

/**
 * WorkflowSpecApiRepository provides read-only access to the workflow_specs table
 * via evolvetrace's HTTP API.
 *
 * The server's GET /api/workflows/:workflowId returns the parsed spec object
 * (already normalized, may carry `facade` and `updatedAt`), not the raw
 * snake_case row. We serialize that object back into WorkflowSpecRow.spec_json so
 * consumers (packs/resolver.ts) can JSON.parse it and re-normalize idempotently.
 */
export class WorkflowSpecApiRepository implements IWorkflowSpecRepository {
  constructor(private api: ApiClient) {}

  async findByWorkflowId(workflowId: string): Promise<WorkflowSpecRow | null> {
    try {
      const resp = await this.api.get<Record<string, unknown>>(
        `/api/workflows/${encodeURIComponent(workflowId)}`,
      );
      if (!resp.ok || !resp.data) return null;

      // The server returns the parsed spec object. If it only wraps a YAML string
      // under { content }, keep that wrapper untouched so the consumer's existing
      // unwrap-and-parse path handles it the same way as DB rows.
      const spec = resp.data;
      const updatedAtMs = typeof spec.updatedAt === "number" ? spec.updatedAt : 0;
      const gmtModified = updatedAtMs > 0 ? Math.floor(updatedAtMs / 1000) : null;

      return {
        // API rows have no stable integer id; digest only needs a stable discriminator.
        id: 0,
        workflow_id: workflowId,
        pack_id: (typeof spec.packId === "string" ? spec.packId : null) ?? null,
        spec_json: JSON.stringify(spec),
        gmt_create: 0,
        gmt_modified: gmtModified,
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[WorkflowSpecApi] findByWorkflowId failed: ${msg}`);
      return null;
    }
  }
}
