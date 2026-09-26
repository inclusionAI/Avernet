import type { PrivateContextParseResult, SpeechPrivateContext, SpeechValidationResult, VotePrivateContext } from './types';
const OPEN = '[UNDERCOVER_UI_CONTEXT_V1]';
const CLOSE = '[/UNDERCOVER_UI_CONTEXT_V1]';
const ANY_OPEN = /\[UNDERCOVER_UI_CONTEXT_V(\d+)\]/;
function record(value: unknown): Record<string, unknown> | undefined { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
function int(value: unknown, min = 0): number | undefined { return typeof value === 'number' && Number.isInteger(value) && value >= min ? value : undefined; }
export function parsePrivateActionContext(instruction?: string): PrivateContextParseResult {
  if (!instruction) return { present: false };
  const version = instruction.match(ANY_OPEN); if (version && version[1] !== '1') return { present: true, error: '暂不支持此操作上下文版本，请刷新或联系主持人。' };
  const start = instruction.indexOf(OPEN); if (start < 0) return { present: false }; const end = instruction.indexOf(CLOSE, start + OPEN.length); if (end < 0) return { present: true, error: '操作上下文无法读取，请刷新后重试。' };
  let value: unknown; try { value = JSON.parse(instruction.slice(start + OPEN.length, end).trim()); } catch { return { present: true, error: '操作上下文无法读取，请刷新后重试。' }; }
  const v = record(value); if (!v) return { present: true, error: '操作上下文无法读取，请刷新后重试。' }; const round = int(v.round); const seatNumber = int(v.seatNumber, 1); if (!round || !seatNumber) return { present: true, error: '操作上下文缺少必要信息，请刷新后重试。' }; const word = typeof v.word === 'string' ? v.word : undefined;
  if (v.action === 'speech') { const maxChars = int(v.maxChars, 1); if (!maxChars || typeof v.forbidOwnWord !== 'boolean') return { present: true, error: '发言规则无法读取，请刷新后重试。' }; const context: SpeechPrivateContext = { version: 1, action: 'speech', round, seatNumber, word, maxChars, forbidOwnWord: v.forbidOwnWord, bluntness: int(v.bluntness, 1) }; return { present: true, context }; }
  if (v.action === 'vote') { const context: VotePrivateContext = { version: 1, action: 'vote', round, seatNumber, word, allowAbstain: v.allowAbstain !== false }; return { present: true, context }; }
  return { present: true, error: '操作类型不受支持，请刷新后重试。' };
}
export function unicodeLength(value: string): number { return Array.from(value).length; }
export function validateSpeech(value: string, maxChars: number, ownWord?: string, forbidOwnWord = false): SpeechValidationResult { const trimmed = value.trim(); const count = unicodeLength(trimmed); if (!trimmed) return { valid: false, error: 'empty', count }; if (count > maxChars) return { valid: false, error: 'too_long', count }; if (forbidOwnWord && ownWord && trimmed.includes(ownWord)) return { valid: false, error: 'contains_own_word', count }; return { valid: true, count }; }
export function serializeVoteTarget(actorId: string): string { if (!actorId.trim()) throw new Error('A vote target is required.'); return JSON.stringify({ kind: 'vote', target_actor_id: actorId.trim() }); }
export function serializeVoteAbstain(): string { return JSON.stringify({ kind: 'vote', abstain: true }); }
