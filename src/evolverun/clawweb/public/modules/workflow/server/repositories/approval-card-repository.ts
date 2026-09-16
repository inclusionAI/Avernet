/**
 * Approval card repository — DB-backed CRUD for card-web approval cards.
 *
 * Table: approval_cards
 *   - id: auto-increment primary key (used in URLs: /approval/:id)
 *   - flow_id, node_id, workflow_id: link back to workflow runtime workflow
 *   - card_fields_json: JSON array of {label, value} for display
 *   - approver_ids: comma-separated empIds
 *   - approved_by / rejected_by: comma-separated empIds (appended on action)
 *   - status: pending | approved | rejected
 */

import type { IDatabase } from "@avernet/clawweb-shared/server/db";

function logDbError(context: string, err: unknown): void {
  const e = err as Record<string, unknown>;
  console.error(`[approval-card] ${context} failed:`, {
    message: e?.message ?? String(err),
    code: (e as any)?.code,
    sql: (e as any)?.sql,
    sqlMessage: (e as any)?.sqlMessage,
    stack: (err instanceof Error ? err.stack : undefined),
  });
}

// ── Types ──────────────────────────────────────────────────────────────

export type ApprovalCardRow = {
  id: number;
  flow_id: string;
  node_id: string;
  workflow_id: string;
  workflow_title: string | null;
  approval_type: string | null;
  message: string | null;
  card_fields_json: string | null;
  approver_ids: string;
  approver_names: string | null;
  approval_policy: string;
  approved_by: string;
  rejected_by: string;
  status: string;
  delivery_mode: string;
  created_at: number;
  resolved_at: number | null;
  /** Structured or plain-text comment from the approver. */
  comment: string | null;
};

export type ApprovalCardCreate = {
  flowId: string;
  nodeId: string;
  workflowId: string;
  workflowTitle?: string;
  approvalType?: string;
  message?: string;
  /** Legacy shape: Array<{label, value}> or unified shape: {fields, sections}. */
  cardFields?: unknown;
  approverIds: string[];
  approverNames?: string[];
  approvalPolicy?: string;
  deliveryMode?: string;
};

export type ApprovalActionResult = {
  ok: boolean;
  status: "pending" | "approved" | "rejected";
  approvedBy: string[];
  rejectedBy: string[];
  error?: string;
};

// ── Repository ─────────────────────────────────────────────────────────

export class ApprovalCardRepository {
  constructor(private db: IDatabase) {}

  /** Create a new approval card. Returns the inserted row ID. */
  async create(card: ApprovalCardCreate): Promise<number> {
    try {
    // created_at is BIGINT (unix timestamp) in MySQL / INTEGER in SQLite — always use integer.
    // Do NOT use nowForDb() which returns datetime strings for MySQL TIMESTAMP columns.
    const now = Math.floor(Date.now() / 1000);
    const sql = `
      INSERT INTO approval_cards
        (flow_id, node_id, workflow_id, workflow_title, approval_type, message,
         card_fields_json, approver_ids, approver_names, approval_policy,
         approved_by, rejected_by, status, delivery_mode, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 'pending', ?, ?)
    `;
    const result = await this.db.exec(sql, [
      card.flowId,
      card.nodeId,
      card.workflowId,
      card.workflowTitle ?? null,
      card.approvalType ?? null,
      card.message ?? null,
      card.cardFields ? JSON.stringify(card.cardFields) : null,
      card.approverIds.join(","),
      card.approverNames?.join(",") ?? null,
      card.approvalPolicy ?? "any",
      card.deliveryMode ?? "card-web",
      now,
    ]);
    return result.insertId ?? 0;
    } catch (err) {
      logDbError("create", err);
      throw err;
    }
  }

  /** Find an approval card by its ID. */
  async findById(id: number): Promise<ApprovalCardRow | null> {
    const rows = await this.db.query<ApprovalCardRow>(
      "SELECT * FROM approval_cards WHERE id = ?",
      [id],
    );
    return rows[0] ?? null;
  }

  /** Find approval cards by flowId + nodeId. */
  async findByFlowNode(flowId: string, nodeId: string): Promise<ApprovalCardRow[]> {
    return this.db.query<ApprovalCardRow>(
      "SELECT * FROM approval_cards WHERE flow_id = ? AND node_id = ? ORDER BY created_at DESC",
      [flowId, nodeId],
    );
  }

  /** Check if an empId is in the approver list for a card. */
  isApprover(card: ApprovalCardRow, empId: string): boolean {
    const ids = card.approver_ids.split(",").map((s) => s.trim());
    return ids.includes(empId);
  }

  /** Parse approved_by / rejected_by comma-separated strings into arrays. */
  parseList(csv: string | null): string[] {
    if (!csv) return [];
    return csv.split(",").map((s) => s.trim()).filter(Boolean);
  }

  /**
   * Record an approval/rejection action by an approver.
   *
   * - Validates the approver is authorized
   * - Idempotent: no-op if the user already performed this action
   * - Evaluates approval policy after the action
   * - Updates status to "approved" or "rejected" if policy is met
   *
   * Returns the result including the updated status.
   */
  async recordAction(
    cardId: number,
    empId: string,
    action: "approve" | "reject",
    comment?: string,
  ): Promise<ApprovalActionResult> {
    const card = await this.findById(cardId);
    if (!card) {
      return { ok: false, status: "pending", approvedBy: [], rejectedBy: [], error: "审批记录未找到" };
    }

    if (!this.isApprover(card, empId)) {
      return { ok: false, status: "pending" as const, approvedBy: [], rejectedBy: [], error: "您不是此审批的授权审批人" };
    }

    if (card.status !== "pending") {
      const approvedBy = this.parseList(card.approved_by);
      const rejectedBy = this.parseList(card.rejected_by);
      return { ok: false, status: card.status as "approved" | "rejected", approvedBy, rejectedBy, error: "审批已处理" };
    }

    const approvedBy = this.parseList(card.approved_by);
    const rejectedBy = this.parseList(card.rejected_by);
    const approverIds = this.parseList(card.approver_ids);

    // Idempotent: already performed this exact action
    if (action === "approve" && approvedBy.includes(empId)) {
      return this.evaluatePolicy(approverIds, approvedBy, rejectedBy, card.approval_policy);
    }
    if (action === "reject" && rejectedBy.includes(empId)) {
      return this.evaluatePolicy(approverIds, approvedBy, rejectedBy, card.approval_policy);
    }

    // Apply action
    if (action === "approve") {
      approvedBy.push(empId);
      // Remove from rejectedBy if switching
      const idx = rejectedBy.indexOf(empId);
      if (idx >= 0) rejectedBy.splice(idx, 1);
    } else {
      rejectedBy.push(empId);
      // Remove from approvedBy if switching
      const idx = approvedBy.indexOf(empId);
      if (idx >= 0) approvedBy.splice(idx, 1);
    }

    // Evaluate policy
    const policyResult = this.evaluatePolicy(approverIds, approvedBy, rejectedBy, card.approval_policy);

    // Update database
    const newStatus = policyResult.status;
    // resolved_at is BIGINT (unix timestamp) in MySQL / INTEGER in SQLite — always use integer.
    const resolvedAt = newStatus !== "pending" ? Math.floor(Date.now() / 1000) : null;

    await this.db.exec(
      `UPDATE approval_cards
       SET approved_by = ?, rejected_by = ?, status = ?, resolved_at = COALESCE(?, resolved_at),
           comment = COALESCE(?, comment)
       WHERE id = ?`,
      [
        approvedBy.join(","),
        rejectedBy.join(","),
        newStatus,
        resolvedAt,
        comment ?? null,
        cardId,
      ],
    ).catch((err) => {
      logDbError("recordAction", err);
      throw err;
    });

    return policyResult;
  }

  /**
   * Evaluate the approval policy given the current state.
   */
  evaluatePolicy(
    approverIds: string[],
    approvedBy: string[],
    rejectedBy: string[],
    policy: string,
  ): ApprovalActionResult {
    const total = approverIds.length;

    switch (policy) {
      case "any": {
        // First action wins
        if (approvedBy.length > 0) {
          return { ok: true, status: "approved", approvedBy, rejectedBy };
        }
        if (rejectedBy.length > 0) {
          return { ok: true, status: "rejected", approvedBy, rejectedBy };
        }
        return { ok: true, status: "pending", approvedBy, rejectedBy };
      }

      case "all": {
        // Any rejection → rejected; all approve → approved
        if (rejectedBy.length > 0) {
          return { ok: true, status: "rejected", approvedBy, rejectedBy };
        }
        if (approvedBy.length >= total) {
          return { ok: true, status: "approved", approvedBy, rejectedBy };
        }
        return { ok: true, status: "pending", approvedBy, rejectedBy };
      }

      case "majority": {
        // >50% approve → approved; any rejection when can't reach majority → rejected
        const threshold = Math.floor(total / 2) + 1;
        if (approvedBy.length >= threshold) {
          return { ok: true, status: "approved", approvedBy, rejectedBy };
        }
        if (rejectedBy.length > 0) {
          return { ok: true, status: "rejected", approvedBy, rejectedBy };
        }
        return { ok: true, status: "pending", approvedBy, rejectedBy };
      }

      default:
        return { ok: true, status: "pending", approvedBy, rejectedBy };
    }
  }

  /** Find resolved (non-pending) card-web cards that workflow runtime should process. */
  async findResolvedCardWeb(limit = 50): Promise<ApprovalCardRow[]> {
    return this.db.query<ApprovalCardRow>(
      `SELECT * FROM approval_cards
       WHERE status != 'pending' AND delivery_mode = 'card-web'
       ORDER BY resolved_at DESC LIMIT ?`,
      [limit],
    );
  }

  /** Mark a card as processed by workflow runtime (set resolved_at or a processed flag). */
  async markProcessed(cardId: number): Promise<void> {
    // We use a soft approach: if resolved_at is set, workflow runtime has the data.
    // No extra column needed — workflow runtime uses (flowId, nodeId, status) to resume.
    await this.db.exec(
      "UPDATE approval_cards SET gmt_modified = ? WHERE id = ?",
      [this.db.dialect.now(), cardId],
    );
  }
}