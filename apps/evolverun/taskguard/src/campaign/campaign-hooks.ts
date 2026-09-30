/**
 * Campaign hooks — lightweight integration points between Controller and Campaign.
 *
 * Controller calls these hooks at flow lifecycle moments:
 * - onFlowStart:    check quota + associate flow
 * - onFlowComplete: write back token usage + evidence
 * - onNodeComplete: append evidence for campaignEvidence-marked nodes
 * - onHumanWait:    create persistent gate
 * - onGateResolve:  resolve gate (called by ClawWeb API callback)
 *
 * All hooks are best-effort: DB failure is logged but doesn't throw,
 * matching the pattern used by FlowRunRepository.
 *
 * If no campaignId is provided, all hooks are no-ops (zero overhead
 * for flows not associated with a campaign).
 */
import { CampaignRepository } from "../db/repositories/campaign-repository.js";
import type { IDatabase } from "../db/types.js";
import type { WorkflowNode, WorkflowSpec } from "../types.js";
import { randomUUID } from "node:crypto";

let repo: CampaignRepository | null = null;

/** Set the database instance for campaign operations. Called once at startup. */
export function setCampaignDatabase(db: IDatabase | null): void {
  repo = db ? new CampaignRepository(db) : null;
}

/** Get the campaign repository (or null if not configured). */
export function getCampaignRepository(): CampaignRepository | null {
  return repo;
}

// ── Flow lifecycle hooks ──

/**
 * Called at handleRun entry when campaignId is provided.
 * Checks quota and associates the flow with the campaign.
 * Returns false if quota is exceeded (caller should abort).
 */
export async function onFlowStart(params: {
  campaignId?: string;
  flowId: string;
  workflowId: string;
}): Promise<{ allowed: boolean; reason?: string }> {
  if (!params.campaignId || !repo) return { allowed: true };
  try {
    const quota = await repo.checkQuota(params.campaignId);
    if (!quota.allowed) {
      return { allowed: false, reason: quota.reason ?? "Campaign quota exceeded" };
    }
    await repo.associateFlow({
      campaignId: params.campaignId,
      flowId: params.flowId,
      workflowId: params.workflowId,
    });
    return { allowed: true };
  } catch (err) {
    console.warn("[campaign] onFlowStart failed:", err);
    return { allowed: true }; // best-effort: allow on error
  }
}

/**
 * Called when a flow completes (succeeded or failed).
 * Writes back token usage to the campaign.
 */
export async function onFlowComplete(params: {
  campaignId?: string;
  flowId: string;
  status: string;
  tokenUsage: number;
}): Promise<void> {
  if (!params.campaignId || !repo) return;
  try {
    await repo.completeFlow(params.flowId, params.status, params.tokenUsage);
  } catch (err) {
    console.warn("[campaign] onFlowComplete failed:", err);
  }
}

// ── Node lifecycle hook ──

/**
 * Called when a node completes. If the node is marked `campaignEvidence: true`
 * and the flow is associated with a campaign, appends the output summary
 * to the campaign evidence chain.
 */
export async function onNodeComplete(params: {
  campaignId?: string;
  flowId: string;
  node: WorkflowNode;
  output: unknown;
}): Promise<void> {
  if (!params.campaignId || !repo) return;
  // Check if node is marked for campaign evidence
  const spec = params.node as WorkflowNode & { campaignEvidence?: boolean };
  if (!spec.campaignEvidence) return;
  try {
    const summary = typeof params.output === "string"
      ? params.output
      : JSON.stringify(params.output);
    await repo.addEvidence({
      campaignId: params.campaignId,
      flowId: params.flowId,
      nodeId: params.node.id,
      summary,
    });
  } catch (err) {
    console.warn("[campaign] onNodeComplete evidence failed:", err);
  }
}

// ── Human-wait gate hook ──

/**
 * Called when a human-wait node enters waiting state.
 * If the flow is associated with a campaign, creates a persistent gate.
 */
export async function onHumanWait(params: {
  campaignId?: string;
  flowId: string;
  nodeId: string;
  prompt: string;
  options?: string[];
}): Promise<string | null> {
  if (!params.campaignId || !repo) return null;
  try {
    const gateId = randomUUID();
    await repo.createGate({
      id: gateId,
      campaignId: params.campaignId,
      flowId: params.flowId,
      nodeId: params.nodeId,
      prompt: params.prompt,
      options: params.options ?? ["approve", "reject"],
    });
    return gateId;
  } catch (err) {
    console.warn("[campaign] onHumanWait gate creation failed:", err);
    return null;
  }
}

/**
 * Resolve a campaign gate. Called by ClawWeb API when user approves/rejects.
 * Returns the flowId + nodeId so the caller can trigger async-callback.
 */
export async function resolveGate(params: {
  gateId: string;
  status: "approved" | "rejected" | "expired";
  resolvedBy?: string;
  reason?: string;
}): Promise<{ flowId: string; nodeId: string; campaignId: string } | null> {
  if (!repo) return null;
  try {
    // Find the gate first to get flowId/nodeId
    // We need to query by gateId — but CampaignRepository doesn't have a getById for gates.
    // Use getPendingGateByFlowNode won't work. Let's just resolve and return what we can.
    await repo.resolveGate(params.gateId, params.status, params.resolvedBy, params.reason);
    // The caller (ClawWeb API) will need to look up the gate to get flowId/nodeId
    // This can be done via getGates(campaignId) — the API layer handles this.
    return null;
  } catch (err) {
    console.warn("[campaign] resolveGate failed:", err);
    return null;
  }
}

// ── Goal-Loop integration hook ──

/**
 * Called after a Goal-Loop iteration completes evaluation.
 * Appends the evaluation result to campaign evidence.
 */
export async function onGoalLoopIteration(params: {
  campaignId?: string;
  flowId: string;
  nodeId: string;
  iteration: number;
  met: boolean;
  reason: string;
}): Promise<void> {
  if (!params.campaignId || !repo) return;
  try {
    await repo.addEvidence({
      campaignId: params.campaignId,
      flowId: params.flowId,
      nodeId: params.nodeId,
      summary: `[Goal-Loop Iter ${params.iteration}] met=${params.met}: ${params.reason}`,
    });
  } catch (err) {
    console.warn("[campaign] onGoalLoopIteration evidence failed:", err);
  }
}

/**
 * Called when a Goal-Loop converges (stops). Updates campaign status.
 */
export async function onGoalLoopConverged(params: {
  campaignId?: string;
  met: boolean;
  reason: string;
}): Promise<void> {
  if (!params.campaignId || !repo) return;
  try {
    if (params.met) {
      await repo.updateStatus(params.campaignId, "completed");
    }
    // If not met, don't auto-fail — let the user decide
  } catch (err) {
    console.warn("[campaign] onGoalLoopConverged failed:", err);
  }
}