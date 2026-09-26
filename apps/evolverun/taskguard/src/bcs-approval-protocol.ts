export {
  BCS_COLLABORATION_PROTOCOL_VERSION as BCS_APPROVAL_PROTOCOL_VERSION,
  WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION as WORKFLOW_APPROVAL_BATCH_PROTOCOL_VERSION,
  WORKFLOW_COLLABORATION_PROTOCOL_VERSION as WORKFLOW_APPROVAL_PROTOCOL_VERSION,
  buildBcsCollaborationBatch as buildBcsApprovalBatch,
  classifyBcsCollaborationMessage as classifyBcsApprovalMessage,
  extractBcsCollaborationMessage as extractBcsApprovalMessage,
  renderWorkflowCollaborationSubject as renderWorkflowApprovalSubject,
} from "./bcs-collaboration-protocol.js";

export type {
  BcsCollaborationBatch as BcsApprovalBatch,
  BcsCollaborationBatchRequest as BcsApprovalBatchRequest,
  BcsCollaborationErrorMessage as BcsApprovalErrorMessage,
  BcsCollaborationMessage as BcsApprovalMessage,
  BcsCollaborationMessageClassification as BcsApprovalMessageClassification,
  BcsCollaborationRequestItem as BcsApprovalRequestItem,
  BcsCollaborationResultMessage as BcsApprovalResultMessage,
  BcsCollaborationTaskState as BcsApprovalState,
  BcsRouteTarget,
  WorkflowCollaborationSubject as WorkflowApprovalSubject,
} from "./bcs-collaboration-protocol.js";
