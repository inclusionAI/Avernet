/**
 * Hermes Adapter — extends McpServerAdapter for Hermes SSE transport.
 *
 * Adds three Hermes-specific capabilities:
 * 1. SSE chatInject — push workflow-progress events to browser client
 * 2. Approval UI — route workflow_confirm to Hermes console approval flow
 * 3. Multi-tenant session isolation — namespace sessionKey with tenantId/teamId
 *
 * Transport (SSE vs stdio) is handled by hermes-entry.ts, not this adapter.
 * This adapter is transport-agnostic — it only needs the SSE send callback.
 *
 * @module platform/hermes-adapter
 */

import {
  createMcpServerAdapter,
  type McpServerAdapterOptions,
} from "./mcp-adapter.js";
import type { PlatformAdapter, ChatInjectSSE, HermesAdapterOptions } from "./types.js";
import { PLATFORM_CAPABILITIES } from "./types.js";

// Re-export HermesAdapterOptions from types for convenience
export type { HermesAdapterOptions } from "./types.js";

/**
 * Create a PlatformAdapter for Hermes (MCP SSE transport).
 *
 * This extends McpServerAdapter with:
 * - SSE-based chatInject (pushes events to browser)
 * - Approval UI integration (workflow_confirm -> Hermes console)
 * - Multi-tenant session key namespacing (tenantId:sessionKey)
 */
export function createHermesAdapter(
  options: McpServerAdapterOptions & HermesAdapterOptions,
): PlatformAdapter & { chatInject: ChatInjectSSE; approvalRequest?: HermesAdapterOptions["approvalRequest"] } {
  const { tenantId, teamId, sseSend, approvalRequest } = options;

  // ── Multi-tenant session key namespacing ──
  // When tenantId or teamId is provided, namespace the sessionKey
  // to isolate flow state per tenant/team.
  // tenantId takes precedence over teamId when both are provided.
  const namespacedSessionKey = tenantId
    ? `${tenantId}:${options.sessionKey}`
    : teamId
      ? `${teamId}:${options.sessionKey}`
      : options.sessionKey;

  // ── Build the base MCP adapter with namespaced session ──
  const { adapter: baseAdapter, wsTeClawProvider } = createMcpServerAdapter({
    ...options,
    sessionKey: namespacedSessionKey,
    // Override chatInject to also push SSE events
    chatInjectFn: sseSend
      ? async (message: string, idempotencyKey: string) => {
          // Log to stderr (from McpServerAdapter default behavior)
          console.error("[clawmind:hermes] chatInject:", message.slice(0, 200));
          // Push SSE event to connected browser client
          try {
            sseSend("workflow-progress", { message, idempotencyKey });
          } catch {
            // Non-blocking — SSE push is best-effort
          }
        }
      : undefined, // Fall back to McpServerAdapter default (stderr + MCP notification)
  });

  // ── Wrap chatInject with SSE pushEvent capability ──
  const chatInject: ChatInjectSSE = {
    inject: baseAdapter.chatInject.inject,
    pushEvent: sseSend
      ? (event: string, data: unknown) => {
          try {
            sseSend(event, data);
          } catch {
            // Non-blocking — SSE push is best-effort
          }
        }
      : undefined,
  };

  // ── Wire approval request into abort.onRequestStop ──
  // When Hermes provides an approval callback and the controller
  // requests a stop, we invoke the approval flow in Hermes console.
  // The approval callback allows the user to confirm or reject via UI.
  const abortWithApproval = {
    ...baseAdapter.abort,
    onRequestStop: approvalRequest
      ? async () => {
          try {
            const result = await approvalRequest(
              baseAdapter.session.sessionKey,
              "abort",
              "Workflow stop requested",
            );
            console.error("[clawmind:hermes] Approval result:", result.approved ? "approved" : "rejected");
          } catch (err) {
            console.error("[clawmind:hermes] Approval request failed:", err);
          }
        }
      : undefined,
  };

  return {
    platform: "hermes",
    taskFlow: baseAdapter.taskFlow,
    chatInject,
    session: baseAdapter.session,
    progress: baseAdapter.progress,
    abort: abortWithApproval,
    capabilities: PLATFORM_CAPABILITIES.hermes,
    transportMode: "http-sse",
    /** Hermes-specific: the approval callback for workflow_confirm via Hermes console. */
    approvalRequest,
  };
}