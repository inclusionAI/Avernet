/**
 * CodeFuse 授权 Token 校验工具。
 *
 * 创建 Agent Coding 模版 Bot 时，CodeFuse 模型依赖用户从授权页复制的
 * Token。前端提交前必须先确认 Token 可解码，且解码结果中的 w 属于
 * 当前登录人，避免误用他人的授权凭证。
 */

export type CodefuseTokenDecodeResult =
  | { ok: true; payload: unknown; decoded: string }
  | { ok: false; reason: 'invalid_base64' | 'invalid_json' };

export const CODEFUSE_TOKEN_ERROR_MESSAGES = {
  missing: '选择了 CodeFuse 模型，请填写授权 Token',
  invalidBase64: '授权 Token 格式错误，请粘贴 CodeFuse 授权页面生成的 Token',
  invalidJson: '授权 Token 内容解析失败，请重新获取 CodeFuse Token',
  missingUser: '授权 Token 缺少 w，请重新获取 CodeFuse Token',
  currentUserMissing: '无法获取当前登录人，请刷新页面后重试',
  userMismatch: '授权 Token 与当前登录人不一致，请使用当前账号的 CodeFuse Token',
} as const;

function normalizeBase64(input: string): string | null {
  const compact = input.replace(/\s/g, '');
  if (!compact || compact.length % 4 === 1) return null;
  if (!/^[A-Za-z0-9+/=_-]+$/.test(compact)) return null;

  const standard = compact.replace(/-/g, '+').replace(/_/g, '/');
  const withoutPadding = standard.replace(/=+$/, '');
  const paddingLength = (4 - (withoutPadding.length % 4)) % 4;
  return `${withoutPadding}${'='.repeat(paddingLength)}`;
}

function decodeBase64Utf8(input: string): string | null {
  const normalized = normalizeBase64(input);
  if (!normalized) return null;

  try {
    if (typeof atob === 'function') {
      const binary = atob(normalized);
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      if (typeof TextDecoder !== 'undefined') {
        return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
      }
      return decodeURIComponent(Array.from(bytes, (byte) => `%${byte.toString(16).padStart(2, '0')}`).join(''));
    }

    if (typeof Buffer !== 'undefined') {
      return Buffer.from(normalized, 'base64').toString('utf-8');
    }
  } catch {
    return null;
  }

  return null;
}

function parseJsonObject(decoded: string): unknown | null {
  try {
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

export function decodeCodefuseToken(token: string): CodefuseTokenDecodeResult {
  const decoded = decodeBase64Utf8(token.trim());
  if (!decoded) return { ok: false, reason: 'invalid_base64' };

  const payload = parseJsonObject(decoded);
  if (payload === null || typeof payload !== 'object') {
    return { ok: false, reason: 'invalid_json' };
  }

  return { ok: true, payload, decoded };
}

function stringifyUserId(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() || null;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return null;
}

export function extractCodefuseTokenUserId(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;

  const visited = new Set<object>();
  const queue: unknown[] = [payload];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || typeof current !== 'object') continue;
    if (visited.has(current)) continue;
    visited.add(current);

    const record = current as Record<string, unknown>;
    const userId = stringifyUserId(record.w);
    if (userId) return userId;

    for (const value of Object.values(record)) {
      if (value && typeof value === 'object') queue.push(value);
    }
  }

  return null;
}

export function validateCodefuseTokenOwner(token: string, currentUserId?: string | null): string | null {
  const trimmedToken = token.trim();
  if (!trimmedToken) return CODEFUSE_TOKEN_ERROR_MESSAGES.missing;

  const decoded = decodeCodefuseToken(trimmedToken);
  if (!decoded.ok) {
    return decoded.reason === 'invalid_base64'
      ? CODEFUSE_TOKEN_ERROR_MESSAGES.invalidBase64
      : CODEFUSE_TOKEN_ERROR_MESSAGES.invalidJson;
  }

  const tokenUserId = extractCodefuseTokenUserId(decoded.payload);
  if (!tokenUserId) return CODEFUSE_TOKEN_ERROR_MESSAGES.missingUser;

  const normalizedCurrentUserId = String(currentUserId || '').trim();
  if (!normalizedCurrentUserId) {
    return CODEFUSE_TOKEN_ERROR_MESSAGES.currentUserMissing;
  }

  if (tokenUserId !== normalizedCurrentUserId) {
    return CODEFUSE_TOKEN_ERROR_MESSAGES.userMismatch;
  }

  return null;
}
