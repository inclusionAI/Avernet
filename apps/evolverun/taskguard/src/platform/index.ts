/**
 * Platform Adapter module — abstracts platform-specific dependencies.
 *
 * Re-exports the public API from the sub-modules.
 *
 * @module platform
 */

export type {
  PlatformAdapter,
  PlatformType,
  EngineName,
  TaskFlowAdapter,
  ChatInjectAdapter,
  ChatInjectSSE,
  SessionAdapter,
  ProgressAdapter,
  AbortAdapter,
  HermesAdapterOptions,
  CapabilityMatrix,
  CommandRunner,
  CommandRunOptions,
  CommandRunResult,
} from "./types.js";

export { PLATFORM_CAPABILITIES, resolveEngineName } from "./types.js";

export type { PluginApi } from "./openclaw-types.js";

export { createOpenClawAdapter, type OpenClawAdapterOptions } from "./openclaw-adapter.js";

export { buildControllerDeps, type AdapterDepsExtras } from "./adapter-to-deps.js";

export { DatabaseTaskFlowAdapter } from "./database-taskflow.js";

export {
  createMcpServerAdapter,
  getMcpEmbeddedAgentFn,
  getMcpCommandRunner,
  type McpServerAdapterOptions,
  type EmbeddedAgentResult,
} from "./mcp-adapter.js";

export { createHermesAdapter } from "./hermes-adapter.js";

export {
  registerWorkflowTools,
  resolveSessionKey,
  formatResult,
  formatError,
  WORKFLOW_TOOL_NAMES,
  type AdapterFactory,
  type AdapterContext,
  type HermesContext,
  type WorkflowToolDeps,
} from "./mcp-tools.js";

export { createDefaultExecutorDispatch, type DefaultExecutorOptions } from "./default-executor.js";

export { getMcpSamplingAgent, type McpSamplingAgentOptions, type McpSamplingParams, type McpSamplingCapable } from "./mcp-sampling-agent.js";

export {
  getMcpAgentRunner,
  detectAgentLoopSupport,
  type McpAgentLoopParams,
  type McpAgentLoopResult,
  type McpAgentLoopCapable,
  type McpSamplingCapable as McpRunnerSamplingCapable,
  type McpAgentRunnerOptions,
} from "./mcp-agent-runner.js";

export { createMcpServerBase, VERSION, type McpServerConfig, type McpServerBase } from "./mcp-server-factory.js";

export { createLogger, type PlatformLogger } from "./logger.js";

// TeClaw Channel 2 (WebSocket /ws/v1/chat)
export { TeClawProvider, createTeClawProviderFromEnv, type TeClawProviderDeps, type WebSocketLike } from "./teclaw-provider.js";
export {
  // WS frame types (preferred)
  type TeClawWsProviderConfig,
  type WsFrame,
  type WsReqFrame,
  type WsResFrame,
  type WsErrorShape,
  type WsEventFrame,
  type WsStateVersion,
  type ConnectPayload,
  type ConnectClientInfo,
  type HelloOkPayload,
  type ChatSendPayload,
  type ChatInjectWsPayload,
  type ChatAbortPayload,
  type ApprovalResolvePayload,
  type ChatEventPayload,
  type ChatEventState,
  type TickEventPayload,
  type ApprovalRequestedEventPayload,
  type ApprovalResolvedEventPayload,
  isWsReqFrame,
  isWsResFrame,
  isWsEventFrame,
  isChatFinalEvent,
  isChatDeltaEvent,
  isChatErrorEvent,
  isChatAbortedEvent,
  isApprovalRequestedEvent,
  isTickEvent,
  validateConnectPayload,
} from "./teclaw-ws-types.js";
export {
  // Legacy HTTP types (deprecated)
  TeClawEventSourceType,
  validateTeClawProviderConfig,
  isLoopCompleteEvent,
  isTextDeltaEvent,
  isToolCallEvent,
  isToolResultEvent,
  type TeClawProviderConfig,
  type TeClawProviderConfigValidation,
  type TeClawAgentLoopParams,
  type TeClawThreadNewResponse,
  type TeClawChatSendResponse,
  type TeClawEventMessage,
  type TeClawLoopCompleteEvent,
  // Still-active types
  type TeClawChatInjectRequest,
  type TeClawChatInjectResponse,
  type TeClawCancelJobResponse,
} from "./teclaw-types.js";

// Note: mcp-entry.ts and hermes-entry.ts are standalone entry points
// (they start MCP servers on import) and are NOT re-exported here.
// They should be invoked directly via:
//   node dist/esm/platform/mcp-entry.js       (stdio transport)
//   node dist/esm/platform/hermes-entry.js    (SSE transport)