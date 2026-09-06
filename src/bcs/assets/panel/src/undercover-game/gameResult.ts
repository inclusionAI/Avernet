import type { UndercoverGameViewModel } from './types';

export interface GameResult {
  title: string;
  summary: string;
  winner: 'civilian' | 'undercover' | 'unknown';
}

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
    const declaration = lines.find(line => /^(游戏结束|本局(?:游戏)?已?结束)(?:[！!。.:：\s]|$)/u.test(line));
    if (!explicitEnd && !declaration) continue;
    const verdicts = [...lines, declaration?.replace(/^(游戏结束|本局(?:游戏)?已?结束)[！!。.:：\s]+/u, '') ?? ''];
    const winner = verdicts.some(line => /^(?:胜利方[：:]\s*)?平民(?:阵营)?(?:获胜|胜利|胜出)[！!。\s]*$/u.test(line))
      ? 'civilian' : verdicts.some(line => /^(?:胜利方[：:]\s*)?卧底(?:阵营)?(?:获胜|胜利|胜出)[！!。\s]*$/u.test(line))
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
