/**
 * CampaignRepository — CRUD + aggregation for campaign tables.
 *
 * Manages campaigns (cross-execution aggregation), campaign_flows (associations),
 * campaign_evidence (evidence chain), and campaign_gates (persistent human approval).
 *
 * Best-effort writes: DB failure is logged but doesn't throw, matching
 * the pattern used by FlowRunRepository.
 */
import type { IDatabase, Row } from "../types.js";
import type {
  Campaign,
  CampaignBudget,
  CampaignEvidence,
  CampaignGate,
  CampaignFlow,
  CampaignStatus,
} from "../../types.js";

// ── Row types (DB snake_case → TS camelCase mapping) ──

type CampaignRow = {
  id: string;
  goal: string;
  status: string;
  budget_max_tokens: number | null;
  budget_max_flows: number | null;
  budget_max_iterations: number | null;
  used_tokens: number;
  used_iterations: number;
  flow_count: number;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
};

type CampaignFlowRow = {
  id: number;
  campaign_id: string;
  flow_id: string;
  workflow_id: string;
  status: string;
  token_usage: number;
  started_at: number;
  completed_at: number | null;
};

type CampaignEvidenceRow = {
  id: number;
  campaign_id: string;
  flow_id: string;
  node_id: string;
  summary: string;
  created_at: number;
};

type CampaignGateRow = {
  id: string;
  campaign_id: string;
  flow_id: string;
  node_id: string;
  prompt: string;
  options_json: string | null;
  status: string;
  reason: string | null;
  resolved_by: string | null;
  created_at: number;
  resolved_at: number | null;
};

// ── Row → Type mappers ──

function rowToCampaign(row: CampaignRow): Campaign {
  return {
    id: row.id,
    goal: row.goal,
    status: row.status as CampaignStatus,
    budget: {
      ...(row.budget_max_tokens != null ? { maxTokens: row.budget_max_tokens } : {}),
      ...(row.budget_max_flows != null ? { maxFlows: row.budget_max_flows } : {}),
      ...(row.budget_max_iterations != null ? { maxIterations: row.budget_max_iterations } : {}),
    },
    usedTokens: row.used_tokens,
    usedIterations: row.used_iterations,
    flowCount: row.flow_count,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    ...(row.completed_at != null ? { completedAt: row.completed_at } : {}),
  };
}

function rowToEvidence(row: CampaignEvidenceRow): CampaignEvidence {
  return {
    id: String(row.id),
    campaignId: row.campaign_id,
    flowId: row.flow_id,
    nodeId: row.node_id,
    summary: row.summary,
    createdAt: row.created_at,
  };
}

function rowToGate(row: CampaignGateRow): CampaignGate {
  return {
    id: row.id,
    campaignId: row.campaign_id,
    flowId: row.flow_id,
    nodeId: row.node_id,
    prompt: row.prompt,
    options: row.options_json ? JSON.parse(row.options_json) : [],
    status: row.status as CampaignGate["status"],
    ...(row.reason ? { reason: row.reason } : {}),
    ...(row.resolved_by ? { resolvedBy: row.resolved_by } : {}),
    createdAt: row.created_at,
    ...(row.resolved_at != null ? { resolvedAt: row.resolved_at } : {}),
  };
}

function rowToCampaignFlow(row: CampaignFlowRow): CampaignFlow {
  return {
    campaignId: row.campaign_id,
    flowId: row.flow_id,
    workflowId: row.workflow_id,
    status: row.status,
    tokenUsage: row.token_usage,
    startedAt: row.started_at,
    ...(row.completed_at != null ? { completedAt: row.completed_at } : {}),
  };
}

// ── Repository ──

export class CampaignRepository {
  constructor(private db: IDatabase) {}

  // ── Campaign CRUD ──

  async create(params: {
    id: string;
    goal: string;
    budget?: CampaignBudget;
  }): Promise<Campaign> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO campaigns (id, goal, status, budget_max_tokens, budget_max_flows, budget_max_iterations, used_tokens, used_iterations, flow_count, created_at, updated_at)
       VALUES (?, ?, 'active', ?, ?, ?, 0, 0, 0, ?, ?)`,
      [
        params.id,
        params.goal,
        params.budget?.maxTokens ?? null,
        params.budget?.maxFlows ?? null,
        params.budget?.maxIterations ?? null,
        now,
        now,
      ],
    );
    return {
      id: params.id,
      goal: params.goal,
      status: "active",
      budget: params.budget ?? {},
      usedTokens: 0,
      usedIterations: 0,
      flowCount: 0,
      createdAt: now,
      updatedAt: now,
    };
  }

  async getById(id: string): Promise<Campaign | null> {
    const rows = await this.db.query<CampaignRow>(
      `SELECT * FROM campaigns WHERE id = ?`,
      [id],
    );
    return rows.length > 0 ? rowToCampaign(rows[0]) : null;
  }

  async list(status?: CampaignStatus): Promise<Campaign[]> {
    const sql = status
      ? `SELECT * FROM campaigns WHERE status = ? ORDER BY updated_at DESC`
      : `SELECT * FROM campaigns ORDER BY updated_at DESC`;
    const params = status ? [status] : [];
    const rows = await this.db.query<CampaignRow>(sql, params);
    return rows.map(rowToCampaign);
  }

  async updateStatus(id: string, status: CampaignStatus): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    const completedAt = status === "completed" || status === "failed" || status === "abandoned" ? now : null;
    await this.db.exec(
      `UPDATE campaigns SET status = ?, updated_at = ?, completed_at = COALESCE(?, completed_at) WHERE id = ?`,
      [status, now, completedAt, id],
    );
  }

  async delete(id: string): Promise<void> {
    await this.db.exec(`DELETE FROM campaign_evidence WHERE campaign_id = ?`, [id]);
    await this.db.exec(`DELETE FROM campaign_gates WHERE campaign_id = ?`, [id]);
    await this.db.exec(`DELETE FROM campaign_flows WHERE campaign_id = ?`, [id]);
    await this.db.exec(`DELETE FROM campaigns WHERE id = ?`, [id]);
  }

  // ── Campaign-Flow association ──

  async associateFlow(params: {
    campaignId: string;
    flowId: string;
    workflowId: string;
  }): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO campaign_flows (campaign_id, flow_id, workflow_id, status, token_usage, started_at)
       VALUES (?, ?, ?, 'running', 0, ?)`,
      [params.campaignId, params.flowId, params.workflowId, now],
    );
    await this.db.exec(
      `UPDATE campaigns SET flow_count = flow_count + 1, updated_at = ? WHERE id = ?`,
      [now, params.campaignId],
    );
  }

  async completeFlow(flowId: string, status: string, tokenUsage: number): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE campaign_flows SET status = ?, token_usage = ?, completed_at = ? WHERE flow_id = ?`,
      [status, tokenUsage, now, flowId],
    );
    // Update campaign aggregate totals
    const flowRows = await this.db.query<{ campaign_id: string }>(
      `SELECT campaign_id FROM campaign_flows WHERE flow_id = ?`,
      [flowId],
    );
    if (flowRows.length > 0) {
      const campaignId = flowRows[0].campaign_id;
      await this.db.exec(
        `UPDATE campaigns SET used_tokens = used_tokens + ?, updated_at = ? WHERE id = ?`,
        [tokenUsage, now, campaignId],
      );
    }
  }

  async getFlows(campaignId: string): Promise<CampaignFlow[]> {
    const rows = await this.db.query<CampaignFlowRow>(
      `SELECT * FROM campaign_flows WHERE campaign_id = ? ORDER BY started_at ASC`,
      [campaignId],
    );
    return rows.map(rowToCampaignFlow);
  }

  // ── Quota check ──

  async checkQuota(campaignId: string): Promise<{
    allowed: boolean;
    reason?: string;
    usedTokens: number;
    maxTokens?: number;
    flowCount: number;
    maxFlows?: number;
  }> {
    const campaign = await this.getById(campaignId);
    if (!campaign) return { allowed: false, reason: "Campaign not found", usedTokens: 0, flowCount: 0 };
    if (campaign.status !== "active") {
      return { allowed: false, reason: `Campaign status is ${campaign.status}`, usedTokens: campaign.usedTokens, flowCount: campaign.flowCount };
    }
    if (campaign.budget.maxFlows && campaign.flowCount >= campaign.budget.maxFlows) {
      return { allowed: false, reason: "Campaign flow count exceeded", usedTokens: campaign.usedTokens, flowCount: campaign.flowCount, maxFlows: campaign.budget.maxFlows };
    }
    // Token check is done with estimated usage at handleRun time — here we just return current state
    return {
      allowed: true,
      usedTokens: campaign.usedTokens,
      flowCount: campaign.flowCount,
      ...(campaign.budget.maxTokens ? { maxTokens: campaign.budget.maxTokens } : {}),
      ...(campaign.budget.maxFlows ? { maxFlows: campaign.budget.maxFlows } : {}),
    };
  }

  // ── Evidence ──

  async addEvidence(params: {
    campaignId: string;
    flowId: string;
    nodeId: string;
    summary: string;
  }): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    const truncated = params.summary.length > 500
      ? params.summary.slice(0, 500) + "... (truncated)"
      : params.summary;
    await this.db.exec(
      `INSERT INTO campaign_evidence (campaign_id, flow_id, node_id, summary, created_at)
       VALUES (?, ?, ?, ?, ?)`,
      [params.campaignId, params.flowId, params.nodeId, truncated, now],
    );
  }

  async getEvidence(campaignId: string, limit = 50, offset = 0): Promise<CampaignEvidence[]> {
    const rows = await this.db.query<CampaignEvidenceRow>(
      `SELECT * FROM campaign_evidence WHERE campaign_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?`,
      [campaignId, limit, offset],
    );
    return rows.map(rowToEvidence);
  }

  // ── Gates ──

  async createGate(params: {
    id: string;
    campaignId: string;
    flowId: string;
    nodeId: string;
    prompt: string;
    options: string[];
  }): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO campaign_gates (id, campaign_id, flow_id, node_id, prompt, options_json, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)`,
      [params.id, params.campaignId, params.flowId, params.nodeId, params.prompt, JSON.stringify(params.options), now],
    );
  }

  async resolveGate(gateId: string, status: "approved" | "rejected" | "expired", resolvedBy?: string, reason?: string): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE campaign_gates SET status = ?, resolved_by = ?, reason = ?, resolved_at = ? WHERE id = ?`,
      [status, resolvedBy ?? null, reason ?? null, now, gateId],
    );
  }

  async getGates(campaignId: string, status?: string): Promise<CampaignGate[]> {
    const sql = status
      ? `SELECT * FROM campaign_gates WHERE campaign_id = ? AND status = ? ORDER BY created_at DESC`
      : `SELECT * FROM campaign_gates WHERE campaign_id = ? ORDER BY created_at DESC`;
    const params = status ? [campaignId, status] : [campaignId];
    const rows = await this.db.query<CampaignGateRow>(sql, params);
    return rows.map(rowToGate);
  }

  async getPendingGateByFlowNode(flowId: string, nodeId: string): Promise<CampaignGate | null> {
    const rows = await this.db.query<CampaignGateRow>(
      `SELECT * FROM campaign_gates WHERE flow_id = ? AND node_id = ? AND status = 'pending' LIMIT 1`,
      [flowId, nodeId],
    );
    return rows.length > 0 ? rowToGate(rows[0]) : null;
  }
}