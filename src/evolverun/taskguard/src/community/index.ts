/**
 * Community default implementations and extension point interfaces.
 *
 * Corp extensions implement TaskguardExtensions to override these defaults.
 */
export type { TaskguardExtensions } from "./types.js";
export { createCommunityDatabase } from "./db-factory.js";
export { createCommunityNotifier } from "./alert-notifier.js";
export { createCommunityApprovalProvider } from "./approval-provider.js";
export { createCommunityKnowledgeAdapters } from "./knowledge-adapters.js";
export { loadCommunityConfig } from "./config-loader.js";
