/**
 * User identity resolution for ClawMind workflow engine.
 *
 * Combines ChannelInfo from channel-detector with identity sources
 * (credentials, env, delivery context, workflow defaults)
 * to produce a complete UserIdentity.
 *
 * Reuses resolveRuntimeUserContext() for non-channel identity sources.
 */

import type { UserIdentity, WorkflowSpec } from "../types.js";
import {
  detectChannelInfo,
  detectChannelInfoFromMessages,
  type DetectOptions,
} from "./channel-detector.js";
import { resolveRuntimeUserContext } from "./user-context.js";

export type ResolveUserIdentityParams = {
  /** Messages array from the conversation (may contain DingTalk metadata) */
  messages?: unknown[];
  /** Raw user message content (alternative to messages array) */
  prompt?: string;
  /** The gen_ai.conversation.id value / sessionKey */
  sessionKey: string;
  /** Bot owner ID from ~/.credentials OWNER_ID */
  ownerId?: string;
  /** BCS group ID from FlowState */
  bcsGroupId?: string;
  /** Delivery context from OpenClaw (may contain user identity) */
  deliveryContext?: Record<string, unknown>;
  /** Environment variables override (defaults to process.env) */
  env?: Record<string, string | undefined>;
  /** Workflow defaults for user identity */
  workflowDefaults?: WorkflowSpec["defaults"];
};

/**
 * Resolve a fallback user identity from non-channel sources.
 * Uses the same priority as resolveRuntimeUserContext.
 */
function resolveFallbackUser(
  params: ResolveUserIdentityParams
): { id?: string; name?: string } {
  const runtimeUser = resolveRuntimeUserContext({
    deliveryContext: params.deliveryContext,
    workflowDefaults: params.workflowDefaults,
    env: params.env,
  });
  if (runtimeUser) {
    return { id: runtimeUser.id, name: runtimeUser.name };
  }
  return {};
}

/**
 * Resolve the full user identity for the current workflow execution context.
 *
 * Combines:
 * - ChannelInfo from detectChannelInfo() — channel, chat type, sender from metadata
 * - Credential identity (ownerId) for isOwner determination
 * - Fallback sources (delivery context, env, workflow defaults) for senderId/senderName
 *   when channel detection doesn't provide them
 */
export function resolveUserIdentity(
  params: ResolveUserIdentityParams
): UserIdentity {
  const { sessionKey, ownerId, bcsGroupId } = params;
  const detectOpts: DetectOptions = { ownerId, bcsGroupId };

  // Step 1: Detect channel info
  const channelInfo = params.messages
    ? detectChannelInfoFromMessages(
        params.messages as Parameters<typeof detectChannelInfoFromMessages>[0],
        sessionKey,
        detectOpts
      )
    : detectChannelInfo(params.prompt, sessionKey, detectOpts);

  // Step 2: Determine final senderId and senderName
  let senderId = channelInfo.senderId;
  let senderName = channelInfo.senderName;

  // For BCS and Web-owner defaults, fallback sources may provide better identity
  if (
    channelInfo.channel === "bcs" ||
    (senderId === "unknown" && senderName === undefined)
  ) {
    const fallback = resolveFallbackUser(params);
    if (fallback.id && (senderId === "unknown" || !senderId)) {
      senderId = fallback.id;
    }
    if (!senderName && fallback.name) {
      senderName = fallback.name;
    }
  }

  // If delivery context has user info and channel detection didn't get a name, try it
  if (
    !senderName &&
    params.deliveryContext?.user &&
    typeof params.deliveryContext.user === "object"
  ) {
    const deliveryUser = params.deliveryContext.user as Record<string, unknown>;
    if (typeof deliveryUser.name === "string") {
      senderName = deliveryUser.name;
    }
  }

  // When senderId remains "unknown" after all fallbacks, default to ownerId
  if (senderId === "unknown" && ownerId) {
    senderId = ownerId;
  }

  // Step 3: Determine isOwner
  const isOwner = ownerId ? senderId === ownerId : true;

  return {
    senderId,
    senderName,
    channel: channelInfo.channel,
    chatType: channelInfo.chatType,
    groupName: channelInfo.groupName,
    groupChannel: channelInfo.groupChannel,
    bcsGroupId: channelInfo.bcsGroupId,
    ownerId,
    isOwner,
    source: channelInfo.source,
  };
}