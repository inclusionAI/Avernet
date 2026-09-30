/**
 * Alert notifications module barrel export.
 */
export { sendDingTalkAlert } from "./dingtalk.js";
export type { DingTalkSendResult, DingTalkAlertPayload } from "./dingtalk.js";
export { AlertDispatcher } from "./alert-dispatcher.js";
export type { NodeFailureEvent, DispatchResult } from "./alert-dispatcher.js";
export { buildMergedAlertMarkdown } from "./markdown-formatter.js";
export type { NodeFailureAlert } from "./markdown-formatter.js";


export {
  buildFailureNotificationMarkdown,
  buildAggregatedFailureMarkdown,
} from "./failure-notification-formatter.js";
export type {
  FailureNotificationInput,
  AggregatedFailureNotificationInput,
} from "./failure-notification-formatter.js";