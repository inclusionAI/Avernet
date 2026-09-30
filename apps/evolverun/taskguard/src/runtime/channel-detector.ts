import type { ChannelInfo, UserChannel, UserChatType, UserIdentitySource } from "../types.js";

export type DetectOptions = {
  ownerId?: string;
  bcsGroupId?: string;
};

type ConversationMeta = {
  sender_id?: string;
  sender?: string;
  is_group_chat?: boolean;
  group_subject?: string;
  group_channel?: string;
};

type SenderMeta = {
  id?: string;
  name?: string;
  label?: string;
};

type MessageContent = string | Array<{ type: string; text?: string }>;

type Message = {
  role: string;
  content: MessageContent;
};

// ── Regex patterns ──

// Matches LAST "Conversation info (untrusted metadata):" JSON block
const CONVERSATION_INFO_RE =
  /Conversation info \(untrusted metadata\):\s*```json\s*\n([\s\S]*?)\n```/g;

// Matches LAST "Sender (untrusted metadata):" JSON block
const SENDER_INFO_RE =
  /Sender \(untrusted metadata\):\s*```json\s*\n([\s\S]*?)\n```/g;

// ── Helpers ──

/** Extract text content from a message (supports string and array formats). */
function extractText(content: MessageContent): string {
  if (typeof content === "string") {
    return content;
  }
  return content
    .filter((part): part is { type: string; text: string } =>
      typeof part.text === "string"
    )
    .map((part) => part.text)
    .join("\n");
}

/**
 * Parse the LAST "Conversation info (untrusted metadata):" JSON block
 * from message content. Returns null if not found or parse fails.
 */
function parseConversationMeta(content: string): ConversationMeta | null {
  let lastMatch: string | null = null;
  let match: RegExpExecArray | null;
  CONVERSATION_INFO_RE.lastIndex = 0;
  while ((match = CONVERSATION_INFO_RE.exec(content)) !== null) {
    lastMatch = match[1];
  }
  if (!lastMatch) return null;
  try {
    return JSON.parse(lastMatch) as ConversationMeta;
  } catch {
    return null;
  }
}

/**
 * Parse the LAST "Sender (untrusted metadata):" JSON block
 * from message content. Returns null if not found or parse fails.
 */
function parseSenderMeta(content: string): SenderMeta | null {
  let lastMatch: string | null = null;
  let match: RegExpExecArray | null;
  SENDER_INFO_RE.lastIndex = 0;
  while ((match = SENDER_INFO_RE.exec(content)) !== null) {
    lastMatch = match[1];
  }
  if (!lastMatch) return null;
  try {
    return JSON.parse(lastMatch) as SenderMeta;
  } catch {
    return null;
  }
}

/**
 * Scan messages backwards to find the last user-role message content.
 * Returns the extracted text or undefined if no user message found.
 */
function getLastUserContent(messages: Message[]): string | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      return extractText(messages[i].content);
    }
  }
  return undefined;
}

/**
 * Extract user:XXX segment from a sessionKey.
 * Returns the user ID string or undefined if not present.
 */
function extractUserFromSessionKey(sessionKey: string): string | undefined {
  const match = sessionKey.match(/:user:([^:]+)/);
  return match?.[1];
}

// ── Main detection ──

/**
 * Detect channel info from a pre-extracted prompt text and sessionKey.
 *
 * Priority order:
 * 1. Conversation info metadata → DingTalk
 * 2. Sender info metadata → DingTalk
 * 3. sessionKey starts with "group:" → BCS
 * 4. sessionKey contains "user:XXX" → Web
 * 5. Default → Web owner
 */
export function detectChannelInfo(
  prompt: string | undefined,
  sessionKey: string,
  options: DetectOptions = {}
): ChannelInfo {
  const { ownerId, bcsGroupId } = options;

  // 1. Conversation info → DingTalk
  if (prompt) {
    const convMeta = parseConversationMeta(prompt);
    if (convMeta) {
      const isGroup =
        convMeta.is_group_chat === true || convMeta.group_subject !== undefined;
      return {
        senderId: convMeta.sender_id ?? "unknown",
        senderName: convMeta.sender,
        channel: "dingtalk" as UserChannel,
        chatType: isGroup ? ("group" as UserChatType) : ("one_on_one" as UserChatType),
        ...(convMeta.group_subject && { groupName: convMeta.group_subject }),
        ...(convMeta.group_channel && { groupChannel: convMeta.group_channel }),
        source: "conversation-meta" as UserIdentitySource,
      };
    }

    // 2. Sender info → DingTalk 1-on-1
    const senderMeta = parseSenderMeta(prompt);
    if (senderMeta) {
      return {
        senderId: senderMeta.id ?? "unknown",
        senderName: senderMeta.name,
        channel: "dingtalk" as UserChannel,
        chatType: "one_on_one" as UserChatType,
        source: "sender-meta" as UserIdentitySource,
      };
    }
  }

  // 3. BCS group: sessionKey starts with "group:"
  if (sessionKey.startsWith("group:")) {
    return {
      senderId: "unknown",
      channel: "bcs" as UserChannel,
      chatType: "group" as UserChatType,
      ...(bcsGroupId && { bcsGroupId }),
      source: "bcs-session" as UserIdentitySource,
    };
  }

  // 4. Web with user:XXX in sessionKey
  const userId = extractUserFromSessionKey(sessionKey);
  if (userId) {
    const isOwner = ownerId !== undefined && userId === ownerId;
    return {
      senderId: userId,
      channel: "web" as UserChannel,
      chatType: isOwner ? ("owner" as UserChatType) : ("others" as UserChatType),
      source: "session-key" as UserIdentitySource,
    };
  }

  // 5. Default: Web owner
  return {
    senderId: ownerId ?? "unknown",
    channel: "web" as UserChannel,
    chatType: "owner" as UserChatType,
    source: "session-key" as UserIdentitySource,
  };
}

/**
 * Convenience wrapper that extracts prompt text from a messages array
 * before delegating to detectChannelInfo.
 */
export function detectChannelInfoFromMessages(
  messages: Message[],
  sessionKey: string,
  options: DetectOptions = {}
): ChannelInfo {
  const prompt = getLastUserContent(messages);
  return detectChannelInfo(prompt, sessionKey, options);
}