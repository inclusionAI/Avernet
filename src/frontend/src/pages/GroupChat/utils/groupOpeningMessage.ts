const MAX_OPENING_MESSAGE_BYTES = 64 * 1024;

const SUPPORTED_TOKENS = new Set([
  '{{bcs.group_id}}',
  '{{bcs.session_id}}',
  '{{bcs.run_id}}',
  '{{bcs.group_name}}',
  '{{bcs.session_name}}',
]);

export function getGroupOpeningMessageError(
  openingMessage: string,
): string | undefined {
  if (!openingMessage.trim()) return undefined;
  if (new TextEncoder().encode(openingMessage).byteLength > MAX_OPENING_MESSAGE_BYTES) {
    return '自定义开场白不能超过 64 KiB';
  }

  let remainder = openingMessage;
  while (true) {
    const start = remainder.indexOf('{{');
    if (start < 0) return undefined;
    const end = remainder.indexOf('}}', start + 2);
    if (end < 0) return '自定义开场白包含未闭合的模板变量';
    const token = remainder.slice(start, end + 2);
    if (!SUPPORTED_TOKENS.has(token)) {
      return `不支持模板变量 ${token}`;
    }
    remainder = remainder.slice(end + 2);
  }
}

export function buildGroupOpeningMessage(
  openingMessage: string,
): string | undefined {
  return openingMessage.trim() ? openingMessage : undefined;
}
