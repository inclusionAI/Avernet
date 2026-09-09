import type { UndercoverGameViewModel } from './types';

export interface GameResult {
  title: string;
  summary: string;
  winner: 'civilian' | 'undercover' | 'unknown';
}

const END_DECLARATION = /^(游戏结束|本局(?:游戏)?已?结束|终局揭晓)(?:[！!。.:：\s—-]|$)/u;
const END_PREFIX = /^(游戏结束|本局(?:游戏)?已?结束|终局揭晓)[！!。.:：\s—-]+/u;
// A verdict may be followed by a separate explanatory sentence, never a condition.
const CIVILIAN_VERDICT = /^(?:胜利方[：:]\s*)?平民(?:阵营)?(?:获胜|胜利|胜出|赢了)(?:[！!。]|$)/u;
const UNDERCOVER_VERDICT = /^(?:胜利方[：:]\s*)?卧底(?:阵营)?(?:获胜|胜利|胜出|赢了)(?:[！!。]|$)/u;

/** A completed run is only a phase boundary. Require an explicit public finale. */
export function gameResult(model: UndercoverGameViewModel): GameResult | undefined {
  if (model.status !== 'completed') return undefined;
  const explicitEnd = ['complete', 'completed', 'finished', 'game_over'].includes(model.phase.toLowerCase());
  const hostEvents = model.publicEvents.filter(event =>
    event.runId === model.params.runId && event.actorId === model.params.host.actorId
    && model.params.nodeActorMap[event.nodeId] === model.params.host.actorId && !event.pending);
  for (const event of [...hostEvents].reverse()) {
    // Ignore quoted rules, conditional descriptions, player claims and streaming output.
    const lines = event.text.split('\n').map(line => line
      .replace(/[\p{Extended_Pictographic}\uFE0F]/gu, '')
      .replace(/[*#]/g, '').trim());
    const declaration = lines.find(line => END_DECLARATION.test(line));
    if (!explicitEnd && !declaration) continue;
    const verdicts = [...lines, declaration?.replace(END_PREFIX, '') ?? ''];
    const civilian = verdicts.some(line => CIVILIAN_VERDICT.test(line));
    const undercover = verdicts.some(line => UNDERCOVER_VERDICT.test(line));
    if (civilian && undercover) continue;
    const winner = civilian
      ? 'civilian' : undercover
        ? 'undercover' : 'unknown';
    // Free-text compatibility requires both an end declaration and a final verdict.
    if (!explicitEnd && winner === 'unknown') continue;
    const reveal = model.params.display?.showPublicReveal !== false;
    return {
      title: reveal && winner === 'civilian' ? '平民阵营获胜' : reveal && winner === 'undercover' ? '卧底阵营获胜' : '本局游戏结束',
      winner: reveal ? winner : 'unknown',
      summary: reveal && model.params.display?.showHostOutput !== false ? event.text : '',
    };
  }
  return explicitEnd ? { title: '本局游戏结束', winner: 'unknown', summary: '' } : undefined;
}
