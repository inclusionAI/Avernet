import type { WorkflowNode, WorkflowApprover, ApprovalPolicy } from "./types.js";
import { getLegacyApprovalExecutor } from "./legacy-runtime.js";

export type ApproverValidationResult =
  | { valid: true; senderId: string; senderName: string }
  | { valid: false; reason: string; senderId: string };

export function extractSenderIdFromMessage(body: string): string | undefined {
  const convMatch = body.match(/"sender_id"\s*:\s*"(\d+)"/);
  if (convMatch) return convMatch[1];

  const senderMatch = body.match(/\\"id\\"\s*:\s*\\"(\d+)\\"/);
  if (senderMatch) return senderMatch[1];

  const senderIdInResult = body.match(/"reviewerId"\s*:\s*"(\d+)"/);
  if (senderIdInResult) return senderIdInResult[1];

  return undefined;
}

export function validateApprover(params: {
  node: WorkflowNode;
  senderId: string;
  senderName?: string;
}): ApproverValidationResult {
  const executor = getLegacyApprovalExecutor(params.node);
  if (!executor?.approvers || executor.approvers.length === 0) {
    return {
      valid: true,
      senderId: params.senderId,
      senderName: params.senderName ?? params.senderId,
    };
  }

  const matchedApprover = executor.approvers.find(
    (a: WorkflowApprover) => a.empId === params.senderId,
  );

  if (matchedApprover) {
    return {
      valid: true,
      senderId: params.senderId,
      senderName: matchedApprover.name,
    };
  }

  const approverNames = executor.approvers.map((a: WorkflowApprover) => a.name).join("、");
  return {
    valid: false,
    reason: `您不在该审批的审批人列表中，无权操作。审批人：${approverNames}`,
    senderId: params.senderId,
  };
}

export function evaluateApprovalPolicy(params: {
  policy: ApprovalPolicy;
  approvers: Array<{ empId: string; name: string }>;
  approvedResults: Array<{ senderId: string; approved: boolean }>;
}): { passed: boolean; reason: string } {
  const { policy, approvers, approvedResults } = params;

  const approvedCount = approvedResults.filter((r) => r.approved).length;
  const rejectedCount = approvedResults.filter((r) => !r.approved).length;

  switch (policy) {
    case "any":
      if (approvedCount >= 1) {
        return { passed: true, reason: `已有 ${approvedCount} 人同意` };
      }
      break;

    case "all": {
      const totalApprovers = approvers.length;
      if (approvedCount >= totalApprovers) {
        return { passed: true, reason: `全部 ${totalApprovers} 位审批人均已同意` };
      }
      if (rejectedCount > 0) {
        return { passed: false, reason: `已有 ${rejectedCount} 人驳回，无法全员通过` };
      }
      break;
    }

    case "majority": {
      const totalApprovers = approvers.length;
      const majority = Math.floor(totalApprovers / 2) + 1;
      if (approvedCount >= majority) {
        return { passed: true, reason: `多数同意 (${approvedCount}/${totalApprovers})` };
      }
      if (rejectedCount >= majority) {
        return { passed: false, reason: `多数驳回 (${rejectedCount}/${totalApprovers})` };
      }
      break;
    }
  }

  return {
    passed: false,
    reason: `等待更多审批（已同意: ${approvedCount}, 已驳回: ${rejectedCount}）`,
  };
}