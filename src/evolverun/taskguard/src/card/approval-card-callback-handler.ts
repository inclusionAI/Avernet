/**
 * Approval card callback handler — processes approve/reject actions
 * from DingTalk interactive card button clicks.
 *
 * Called by the `/api/approval/callback` endpoint when the DingTalk
 * connector receives a `TOPIC_CARD` callback with actionId=approve/reject.
 *
 * The handler:
 * 1. Looks up the approval card record by outTrackId
 * 2. Validates the user is an authorized approver
 * 3. Records the action and evaluates the approval policy
 * 4. If the policy is met, resolves the workflow node via `handleBcsCallback`
 * 5. Returns the result so the connector can update the card UI
 */

import type { ControllerDeps } from "../controller.js";
import {
  resolveApprovalCard,
  recordApprovalAction,
  markApprovalCardResolved,
  evaluateApprovalPolicy,
  type ApprovalCardRecord,
} from "./approval-card-registry.js";

// ── Types ──────────────────────────────────────────────────────────────

export type ApprovalCallbackParams = {
  outTrackId: string;
  action: "approve" | "reject";
  userId: string;
};

export type ApprovalCallbackResult = {
  ok: boolean;
  error?: string;
  message?: string;
  /** Current approval status after processing this action */
  status?: "pending" | "approved" | "rejected";
};

// ── Handler ────────────────────────────────────────────────────────────

/**
 * Process an approval card callback from DingTalk.
 *
 * @param deps - ControllerDeps for flow resolution
 * @param params - The callback parameters from the DingTalk connector
 * @returns Result with ok=true if processed (even if still pending more approvals)
 */
export async function handleApprovalCardCallback(
  deps: ControllerDeps,
  params: ApprovalCallbackParams,
): Promise<ApprovalCallbackResult> {
  const { outTrackId, action, userId } = params;

  // 1. Look up the approval card record
  const record = resolveApprovalCard(outTrackId);
  if (!record) {
    return {
      ok: false,
      error: "approval_card_not_found",
      message: `审批卡片 ${outTrackId} 未找到或已过期`,
    };
  }

  // 2. Already resolved?
  if (record.status !== "pending") {
    return {
      ok: false,
      error: "already_resolved",
      message: `审批已处理: ${record.status === "approved" ? "已通过" : "已驳回"}`,
    };
  }

  // 3. Validate the user is an authorized approver
  if (!record.approverIds.includes(userId)) {
    return {
      ok: false,
      error: "unauthorized",
      message: `用户 ${userId} 不在审批人列表中`,
    };
  }

  // 4. Record the action (idempotent — no-op if user already performed this action)
  const updated = recordApprovalAction(outTrackId, userId, action);
  if (!updated) {
    // This can happen if the record was resolved between steps 2 and 4
    return {
      ok: false,
      error: "action_record_failed",
      message: "审批操作记录失败，卡片可能已被其他审批人处理",
    };
  }

  // 5. Evaluate the approval policy
  const policyResult = evaluateApprovalPolicy(updated);

  if (!policyResult.passed) {
    // Policy not yet met — more approvals needed
    const actionLabel = action === "approve" ? "已同意" : "已驳回";
    return {
      ok: true,
      message: `${userId} ${actionLabel}。${policyResult.reason}`,
      status: "pending",
    };
  }

  // 6. Policy met — resolve the workflow node
  try {
    const isApproved = policyResult.status === "approved";
    const resultPayload = {
      approved: isApproved,
      reviewer: userId,
      reviewTime: new Date().toISOString(),
      note: isApproved
        ? `审批通过 (${updated.approvedBy.join(", ")})`
        : `审批驳回 (${updated.rejectedBy.join(", ")})`,
      source: "dingtalk-card",
    };

    // Use handleBcsCallback which is the generic external callback resolution
    // mechanism — it handles state transition, hooks, and flow resumption.
    const resolutionMessage = await handleBcsCallback(
      deps,
      record.flowId,
      record.nodeId,
      resultPayload,
    );

    // Mark the card as resolved in the registry
    markApprovalCardResolved(outTrackId, isApproved ? "approved" : "rejected");

    return {
      ok: true,
      message: resolutionMessage,
      status: policyResult.status,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[approval-card-callback] Resolution failed", {
      outTrackId,
      flowId: record.flowId,
      nodeId: record.nodeId,
      error: msg,
    });
    return {
      ok: false,
      error: "resolution_failed",
      message: `工作流节点解析失败: ${msg}`,
    };
  }
}

// ── Re-export from controller ──────────────────────────────────────────
// Import handleBcsCallback from controller — it's the generic external
// callback bridge that resolves a waiting node and resumes the flow.

import { handleBcsCallback } from "../controller.js";