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
 *
 * This is a copy of clawweb's ApprovalCardRepository adapted for evolvetrace's
 * IDatabase interface. The approval_cards table is shared between both apps.
 */
import type { IDatabase } from "../db.js";

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
  comment: string | null;
};

export type ApprovalActionResult = {
  ok: boolean;
  status: "pending" | "approved" | "rejected";
  approvedBy: string[];
  rejectedBy: string[];
  error?: string;
};

export class ApprovalCardRepository {
  constructor(private db: IDatabase) {}

  /** Find an approval card by its ID. */
  async findById(id: number): Promise<ApprovalCardRow | null> {
    const rows = await this.db.query<ApprovalCardRow>(
      "SELECT * FROM approval_cards WHERE id = ?",
      [id],
    );
    return rows[0] ?? null;
  }

  /** Find all approval cards for a given flow (run), newest first. */
  async findByFlowId(flowId: string): Promise<ApprovalCardRow[]> {
    return this.db.query<ApprovalCardRow>(
      "SELECT * FROM approval_cards WHERE flow_id = ? ORDER BY created_at DESC",
      [flowId],
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
      const idx = rejectedBy.indexOf(empId);
      if (idx >= 0) rejectedBy.splice(idx, 1);
    } else {
      rejectedBy.push(empId);
      const idx = approvedBy.indexOf(empId);
      if (idx >= 0) approvedBy.splice(idx, 1);
    }

    // Evaluate policy
    const policyResult = this.evaluatePolicy(approverIds, approvedBy, rejectedBy, card.approval_policy);

    // Update database
    const newStatus = policyResult.status;
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
    );

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
        if (approvedBy.length > 0) {
          return { ok: true, status: "approved", approvedBy, rejectedBy };
        }
        if (rejectedBy.length > 0) {
          return { ok: true, status: "rejected", approvedBy, rejectedBy };
        }
        return { ok: true, status: "pending", approvedBy, rejectedBy };
      }
      case "all": {
        if (rejectedBy.length > 0) {
          return { ok: true, status: "rejected", approvedBy, rejectedBy };
        }
        if (approvedBy.length >= total) {
          return { ok: true, status: "approved", approvedBy, rejectedBy };
        }
        return { ok: true, status: "pending", approvedBy, rejectedBy };
      }
      case "majority": {
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
}
