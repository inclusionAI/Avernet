const OPENCLAW_SILENT_REPLY_TOKEN = 'NO_REPLY';
const SILENT_STOP_REASON = 'silent';

type MessageLike = Record<string, unknown>;

function asRecord(value: unknown): MessageLike | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }

  return value as MessageLike;
}

function asTextContent(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (!Array.isArray(value)) return undefined;

  const parts: string[] = [];
  for (const block of value) {
    const item = asRecord(block);
    if (item?.type !== 'text' || typeof item.text !== 'string') {
      return undefined;
    }
    parts.push(item.text);
  }

  return parts.join('');
}

function isLegacySilentToken(value: unknown): boolean {
  const text = asTextContent(value)?.trim();
  if (!text) return false;
  if (text.toUpperCase() === OPENCLAW_SILENT_REPLY_TOKEN) return true;

  try {
    const payload = asRecord(JSON.parse(text));
    if (!payload || Object.keys(payload).length !== 1) return false;

    return (
      typeof payload.action === 'string' &&
      payload.action.trim().toUpperCase() === OPENCLAW_SILENT_REPLY_TOKEN
    );
  } catch {
    return false;
  }
}

/**
 * Returns true only for an assistant message that represents a silent
 * completion. Lifecycle callbacks must still be processed before applying
 * this display-level filter.
 */
export function isSilentAssistantReply(message: unknown): boolean {
  const item = asRecord(message);
  if (!item) return false;

  const extra = asRecord(item.extra);
  const payload = asRecord(extra?.payload);
  const payloadMessage = asRecord(payload?.message);
  const metadata = asRecord(item.metadata);
  const payloadMetadata = asRecord(payload?.metadata);
  const payloadMessageMetadata = asRecord(payloadMessage?.metadata);

  const role = item.role ?? payloadMessage?.role ?? payload?.role;
  if (role !== 'assistant') return false;

  const stopReasons = [
    item.stopReason,
    item.stop_reason,
    metadata?.stopReason,
    metadata?.stop_reason,
    extra?.stopReason,
    extra?.stop_reason,
    payload?.stopReason,
    payload?.stop_reason,
    payloadMetadata?.stopReason,
    payloadMetadata?.stop_reason,
    payloadMessageMetadata?.stopReason,
    payloadMessageMetadata?.stop_reason,
  ];

  if (
    stopReasons.some(
      (reason) =>
        typeof reason === 'string' &&
        reason.trim().toLowerCase() === SILENT_STOP_REASON,
    )
  ) {
    return true;
  }

  const content = item.content ?? payloadMessage?.content ?? payload?.content;
  return isLegacySilentToken(content);
}
