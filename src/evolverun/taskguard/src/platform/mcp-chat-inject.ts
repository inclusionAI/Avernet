/**
 * MCP ChatInject — dual-channel message push per RFC-003 §6.
 *
 * Channel strategy:
 * - progress/info/error: Claude Code Channel notification (fire-and-forget) + WS chat.inject or HTTP (best-effort backup)
 * - approval: WS chat.inject or HTTP only (must-deliver, no Channel notification)
 *
 * When a TeClawProvider (WebSocket) is available, chat.inject is sent via
 * the WebSocket chat.inject method (preferred). Otherwise falls back to
 * HTTP POST to the chat/inject endpoint.
 *
 * @module platform/mcp-chat-inject
 */

import type { ChatInjectAdapter, ChatInjectOptions, ChatInjectAction } from "./types.js";
import { ChatInjectMessageType } from "./types.js";
import type { TeClawProvider } from "./teclaw-provider.js";
import type { TeClawChatInjectContext } from "./teclaw-types.js";
import { bufferWorkflowEvent } from "./workflow-event-buffer.js";

// ── HTTP ChatInject Client ──

/** HTTP response from chat/inject endpoint. */
export interface ChatInjectHttpResponse {
  /** "delivered" for progress/info/error, "pending" for approval. */
  status: "delivered" | "pending";
  /** Channel that handled the message. */
  channel?: "dingtalk" | "web" | "repl";
  /** Approval ID (only for approval type). */
  approvalId?: string;
  /** Timeout in seconds for approval (only for approval type). */
  timeout?: number;
}

/** Configuration for the HTTP chat/inject client. */
export interface HttpChatInjectConfig {
  /** Full URL of the chat/inject endpoint. */
  chatInjectUrl: string;
  /** API key for Bearer auth. */
  chatInjectKey: string;
  /** Custom HTTP client function (for testing). Defaults to global fetch. */
  httpClient?: (url: string, options: { headers?: Record<string, string>; body?: unknown }) => Promise<ChatInjectHttpResponse>;
}

/** Parameters for a chat/inject HTTP request body. */
export interface ChatInjectHttpParams {
  messageType: ChatInjectMessageType;
  message: string;
  idempotencyKey: string;
  flowId?: string;
  nodeId?: string;
  workflowId?: string;
  actions?: ChatInjectAction[];
  metadata?: Record<string, unknown>;
}

/**
 * Create an HTTP client for the chat/inject endpoint.
 * Used internally by createDualChannelChatInject; exposed for direct use.
 */
export function createHttpChatInjectClient(config: HttpChatInjectConfig): (params: ChatInjectHttpParams) => Promise<ChatInjectHttpResponse> {
  const { chatInjectUrl, chatInjectKey, httpClient } = config;

  return async (params: ChatInjectHttpParams): Promise<ChatInjectHttpResponse> => {
    const body: Record<string, unknown> = {
      messageType: params.messageType,
      flowId: params.flowId,
      nodeId: params.nodeId,
      workflowId: params.workflowId,
      message: params.message,
      idempotencyKey: params.idempotencyKey,
      timestamp: new Date().toISOString(),
    };
    if (params.actions && params.actions.length > 0) {
      body.actions = params.actions;
    }
    if (params.metadata) {
      body.metadata = params.metadata;
    }

    if (httpClient) {
      // Test path: use custom client
      return httpClient(chatInjectUrl, {
        headers: {
          "Authorization": `Bearer ${chatInjectKey}`,
          "Content-Type": "application/json",
        },
        body,
      });
    }

    // Production path: use global fetch
    const response = await globalThis.fetch(chatInjectUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${chatInjectKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const text = await response.text().catch(() => "unknown");
      throw new Error(`chat/inject HTTP ${response.status}: ${text}`);
    }

    return (await response.json()) as ChatInjectHttpResponse;
  };
}

// ── Notification ChatInject Helper ──

/** MCP server shape for sending notifications. */
interface NotificationCapable {
  server: {
    notification: (params: unknown) => Promise<void>;
  };
}

/**
 * Build a structured notifications/message payload with `_clawmind` marker.
 * Per RFC-003 §6.4, TeClaw identifies ClawMind notifications by this marker.
 */
function buildNotificationParams(
  message: string,
  idempotencyKey: string,
  options?: ChatInjectOptions,
): { method: string; params: { level: string; data: Record<string, unknown> } } {
  const messageType = options?.messageType ?? ChatInjectMessageType.Progress;
  const level = messageType === ChatInjectMessageType.Error ? "error" : "info";

  return {
    method: "notifications/message",
    params: {
      level,
      data: {
        _clawmind: true,
        messageType,
        flowId: options?.flowId,
        nodeId: options?.nodeId,
        workflowId: options?.workflowId,
        message,
        idempotencyKey,
        timestamp: options?.timestamp ?? new Date().toISOString(),
      },
    },
  };
}

// ── Dual-Channel ChatInject ──

/** Options for the dual-channel chatInject. */
export interface DualChannelChatInjectOptions {
  /** Full URL of the TeClaw chat/inject HTTP endpoint. */
  chatInjectUrl?: string;
  /** API key for Bearer auth to chat/inject. */
  chatInjectKey?: string;
  /** Custom HTTP client (for testing). */
  httpClient?: (url: string, options: { headers?: Record<string, string>; body?: unknown }) => Promise<ChatInjectHttpResponse>;
  /**
   * TeClawProvider instance for WebSocket chat.inject.
   * When provided, chat.inject is sent via WebSocket (preferred over HTTP)
   * and MCP notification (Channel 1) is SKIPPED entirely — WS is the sole
   * notification channel. Falls back to HTTP chatInjectUrl if WS fails.
   */
  teclawProvider?: TeClawProvider;
  /**
   * TeClaw session key for WS chat.inject routing.
   * This is the authoritative session_key from TeClaw (e.g.,
   * `session:<uuid>:user:<bot_id>`), used as the first argument to
   * the WS `chat.inject` method. When absent, falls back to the
   * TeClawProvider's built-in sessionKey.
   */
  teclawSessionKey?: string;
}

/**
 * Create a ChatInjectAdapter that uses the dual-channel strategy.
 *
 * Per RFC-003 §6.2:
 * - progress/info/error: notification + HTTP (dual-send, best-effort both)
 * - approval: HTTP only (must-deliver, never notification)
 *
 * Falls back to stderr-only logging when no MCP server or HTTP config is available.
 */
export function createDualChannelChatInject(
  mcpServer?: NotificationCapable,
  httpOptions?: DualChannelChatInjectOptions,
): ChatInjectAdapter {
  // Create HTTP client if URL is configured (fallback for when WS is unavailable)
  let httpChatInject: ((params: ChatInjectHttpParams) => Promise<ChatInjectHttpResponse>) | undefined;
  if (httpOptions?.chatInjectUrl && httpOptions?.chatInjectKey) {
    httpChatInject = createHttpChatInjectClient({
      chatInjectUrl: httpOptions.chatInjectUrl,
      chatInjectKey: httpOptions.chatInjectKey,
      httpClient: httpOptions.httpClient,
    });
  }

  // TeClawProvider for WebSocket chat.inject (preferred over HTTP)
  const teclawProvider = httpOptions?.teclawProvider;
  // TeClaw session key for WS chat.inject routing — the authoritative key
  // from TeClaw (e.g., "session:<uuid>:user:<bot_id>"). Falls back to the
  // TeClawProvider's built-in sessionKey if not explicitly provided.
  const teclawSessionKey = httpOptions?.teclawSessionKey ?? teclawProvider?.sessionKey;

  return {
    async inject(message: string, idempotencyKey: string, options?: ChatInjectOptions): Promise<void> {
      const messageType = options?.messageType ?? ChatInjectMessageType.Progress;
      const hasTeclawProvider = Boolean(teclawProvider);

      // ── Channel 1: Claude Code Channel notification (NOT for approval) ──
      // When a TeClawProvider (WS) is available, skip Channel notification entirely.
      // WS chat.inject is the sole notification channel — Channel notification
      // fails with "Not connected" on Streamable HTTP after the request
      // session closes, and is redundant when WS is active.
      //
      // When TeClaw is NOT available (Claude Code plugin mode), use the
      // Claude Code Channels API (notifications/claude/channel) instead of
      // the standard MCP notifications/message. Channel events arrive as
      // <channel source="clawmind" ...> tags in Claude's context, which
      // Claude reads and relays to the user. If the session doesn't support
      // channels (no --channels flag), events are silently discarded —
      // harmless fallback equivalent to the old notifications/message behavior.
      if (mcpServer && messageType !== ChatInjectMessageType.Approval && !hasTeclawProvider) {
        try {
          // Build Channel notification with structured meta attributes.
          // Each meta key becomes an attribute on the <channel> tag.
          // Core fields from options, plus extended fields from metadata
          // (nodeIndex, executorType, durationMs, verbosity, etc.) for
          // richer channel events in verbose chatInject mode.
          const meta: Record<string, string> = {};
          if (options?.flowId) meta.flow_id = options.flowId;
          if (options?.nodeId) meta.node_id = options.nodeId;
          if (options?.workflowId) meta.workflow_id = options.workflowId;
          meta.event_type = messageType;
          // Elevate metadata fields to channel meta attributes (string values only)
          if (options?.metadata) {
            const md = options.metadata;
            if (md.nodeIndex != null) meta.node_index = String(md.nodeIndex);
            if (md.executorType) meta.executor_type = String(md.executorType);
            if (md.durationMs != null) meta.duration_ms = String(md.durationMs);
            if (md.verbosity) meta.verbosity = String(md.verbosity);
            if (md.skipReason) meta.skip_reason = String(md.skipReason);
            if (md.outputContractResult) meta.output_contract = String(md.outputContractResult);
          }
          console.error(`[clawmind:mcp] chatInject ch1(channel): type=${messageType} flowId=${options?.flowId ?? "-"} key=${idempotencyKey.slice(0, 40)} meta_keys=${Object.keys(meta).join(",")} msg_len=${message.length}`);
          await mcpServer.server.notification({
            method: "notifications/claude/channel",
            params: { content: message, meta },
          });
          console.error(`[clawmind:mcp] chatInject ch1(channel): DELIVERED`);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.error(`[clawmind:mcp] chatInject ch1(channel): FAILED: ${msg.slice(0, 100)}`);
        }
      } else if (hasTeclawProvider) {
        console.error(`[clawmind:mcp] chatInject ch1(channel): SKIPPED (teclaw WS is sole channel)`);
      }

      // ── Channel 1.5: Event Buffer (polling fallback) ──
      // Always buffer the event so Claude can query it via workflow_recent_events
      // tool. This is the reliable fallback when Channels API is unavailable
      // (e.g., VS Code plugin mode, CLI versions without --channels support).
      // In TeClaw mode, buffering is skipped (WS delivers in real-time).
      if (!hasTeclawProvider && messageType !== ChatInjectMessageType.Approval) {
        bufferWorkflowEvent(message, messageType, {
          flowId: options?.flowId,
          nodeId: options?.nodeId,
          workflowId: options?.workflowId,
        });
      }

      // ── Channel 2: WebSocket chat.inject (PREFERRED) or HTTP chat/inject ──
      let wsInjected = false;
      console.error(`[clawmind:mcp] chatInject ch2(WS): provider=${hasTeclawProvider ? "yes" : "no"} connected=${teclawProvider?.connected ?? false} teclawSessionKey=${teclawSessionKey?.slice(0, 40) ?? "none"} type=${messageType} flowId=${options?.flowId ?? "-"}`);
      if (hasTeclawProvider) {
        // WebSocket chat.inject — sole notification path when teclawProvider exists.
        // chatInjectWS will auto-connect if not yet connected (lazy connection).
        try {
          // Derive a human-readable label from messageType + context
          const label = `${messageType}${options?.nodeId ? `:${options.nodeId}` : ""}`;
          // Use teclawSessionKey (the TeClaw conversation key) for WS routing,
          // NOT flowId — the WS chat.inject method requires the session_key
          // from the TeClaw conversation (e.g., "session:<uuid>:user:<bot_id>")
          // so the server can route the message to the correct client session.
          const wsSessionKey = teclawSessionKey ?? idempotencyKey;
          const structuredContext: TeClawChatInjectContext = {
            messageType,
            flowId: options?.flowId,
            nodeId: options?.nodeId,
            workflowId: options?.workflowId,
            idempotencyKey,
            actions: options?.actions?.map((action) => ({
              label: action.label,
              action: action.action,
              style: action.style ?? "default",
            })),
            metadata: options?.metadata,
            timestamp: options?.timestamp ?? new Date().toISOString(),
          };
          console.error(`[clawmind:mcp] chatInject ch2(WS): sending chat.inject label=${label} sessionKey=${wsSessionKey.slice(0, 40)} msg_len=${message.length}`);
          await teclawProvider!.chatInjectWS(
            wsSessionKey,
            message,
            label,
            structuredContext,
          );
          wsInjected = true;
          console.error(`[clawmind:mcp] chatInject ch2(WS): DELIVERED`);
        } catch (err) {
          // WS chat.inject failed — fall through to HTTP fallback
          const msg = err instanceof Error ? err.message : String(err);
          console.error(`[clawmind:mcp] chatInject ch2(WS): FAILED, falling back to HTTP: ${msg.slice(0, 100)}`);
        }
      }

      // HTTP fallback (or primary if no WS provider or WS failed)
      if (httpChatInject && !wsInjected) {
        console.error(`[clawmind:mcp] chatInject ch2(HTTP): sending url=${httpChatInject ? "configured" : "none"} type=${messageType} flowId=${options?.flowId ?? "-"}`);
        try {
          await httpChatInject({
            messageType,
            message,
            idempotencyKey,
            flowId: options?.flowId,
            nodeId: options?.nodeId,
            workflowId: options?.workflowId,
            actions: options?.actions,
            metadata: options?.metadata,
          });
          console.error(`[clawmind:mcp] chatInject ch2(HTTP): DELIVERED`);
        } catch (err) {
          // HTTP failure for progress/info/error is acceptable (notification was sent)
          // HTTP failure for approval is critical — log loudly
          const errMsg = err instanceof Error ? err.message : String(err);
          if (messageType === ChatInjectMessageType.Approval) {
            console.error(`[clawmind:mcp] chatInject CRITICAL: ch2(HTTP) FAILED for approval: ${errMsg}`);
          } else {
            console.error(`[clawmind:mcp] chatInject ch2(HTTP) FAILED (non-critical): ${errMsg.slice(0, 100)}`);
          }
        }
      }

      // ── Fallback: stderr logging ──
      if (!mcpServer && !httpChatInject && !hasTeclawProvider) {
        console.error(`[clawmind:mcp] chatInject [${messageType}] (no channel): ${message.slice(0, 200)}`);
      }
    },
  };
}
