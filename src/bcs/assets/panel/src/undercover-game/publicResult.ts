import { requestJson } from './api';
import type { GameResult } from './gameResult';
import type { UndercoverGamePanelParams } from './types';

interface SessionFile {
  file_id: string;
  file_name: string;
  session_id: string;
  status: string;
  size: number;
  owner: { actor_kind: string; actor_id: string };
}

export interface PublicGameResult {
  kind: 'undercover.game-result'; version: 1; status: 'finished';
  gameSessionId: string; hostActorId: string; round: number; attempt: number;
  winner: 'civilian' | 'undercover'; reason: string; summary: string;
}

export function parsePublicGameResult(value: unknown, params: UndercoverGamePanelParams): PublicGameResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('终局结果格式无效。');
  const result = value as Record<string, unknown>;
  if (result.kind !== 'undercover.game-result' || result.version !== 1) throw new Error('不支持的终局结果版本。');
  if (result.status !== 'finished' || result.gameSessionId !== (params.gameSessionId ?? params.sessionId)
    || result.hostActorId !== params.host.actorId || result.round !== params.round || result.attempt !== params.attempt
    || !Number.isInteger(result.round) || !Number.isInteger(result.attempt)
    || typeof result.winner !== 'string' || !['civilian', 'undercover'].includes(result.winner)
    || typeof result.reason !== 'string' || !result.reason.trim()
    || typeof result.summary !== 'string' || !result.summary.trim()) throw new Error('终局结果与当前对局不匹配或格式无效。');
  return result as unknown as PublicGameResult;
}

/** Only the authenticated host's ready file in this session is authoritative. */
export async function fetchPublicGameResult(params: UndercoverGamePanelParams, signal: AbortSignal): Promise<PublicGameResult | undefined> {
  if (!params.resultFile) return undefined;
  const path = `/sessions/${encodeURIComponent(params.sessionId)}/files`;
  let offset = 0;
  let accepted: PublicGameResult | undefined;
  do {
    const page: { items: SessionFile[]; total: number } = await requestJson(params.apiBaseUrl ?? '',
      `${path}?prefix=${encodeURIComponent(params.resultFile)}&limit=100&offset=${offset}`, { signal });
    if (!Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0) throw new Error('终局文件列表格式无效。');
    for (const file of page.items) {
      if (file.file_name !== params.resultFile || file.session_id !== params.sessionId || file.status?.toLowerCase() !== 'ready'
        || file.owner?.actor_kind?.toLowerCase() !== 'bot' || file.owner.actor_id !== params.host.actorId) continue;
      if (!file.file_id || !Number.isInteger(file.size) || file.size < 1 || file.size > 65536) throw new Error('终局文件大小无效。');
      const body = await requestJson<unknown>(params.apiBaseUrl ?? '', `${path}/${encodeURIComponent(file.file_id)}/content`, { signal, credentials: 'same-origin' });
      const result = parsePublicGameResult(body, params);
      if (accepted && JSON.stringify(accepted) !== JSON.stringify(result)) throw new Error('存在相互冲突的终局结果。');
      accepted = result;
    }
    offset += page.items.length;
    if (offset >= page.total) return accepted;
    if (!page.items.length || offset >= 1000) throw new Error('终局文件列表未完整加载，请重试。');
  } while (!signal.aborted);
  return undefined;
}

export function publicGameResultView(result: PublicGameResult, params: UndercoverGamePanelParams): GameResult {
  const reveal = params.display?.showPublicReveal !== false;
  return {
    winner: reveal ? result.winner : 'unknown',
    title: reveal ? (result.winner === 'civilian' ? '平民阵营获胜' : '卧底阵营获胜') : '本局游戏结束',
    summary: reveal && params.display?.showHostOutput !== false ? result.summary : '',
  };
}
