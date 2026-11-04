import { isStructuredVote } from './currentRound';
import type { ActorViewModel, UndercoverGameViewModel } from './types';

export type NightStepKey = 'drawing' | 'describe' | 'discuss' | 'vote';
export type NightStepState = 'done' | 'active' | 'pending';

export interface NightStep {
  key: NightStepKey;
  label: string;
  state: NightStepState;
}

const STEPS: ReadonlyArray<{ key: NightStepKey; label: string }> = [
  { key: 'drawing', label: '发词' },
  { key: 'describe', label: '描述' },
  { key: 'discuss', label: '讨论' },
  { key: 'vote', label: '投票' },
];

/**
 * Map the script-authored phase onto the four public night-banquet beats.
 * Completion counts come from the real graph, so a finished speech round shows
 * "讨论" while the host prepares the next phase instead of a fake timer.
 */
export function nightActiveStep(phase: string, completedTurns: number, totalTurns: number, terminal: boolean): number {
  if (terminal) return STEPS.length;
  const lower = phase.toLowerCase();
  if (lower.includes('vot')) return 3;
  if (lower.includes('discuss')) return 2;
  if (lower.includes('speak') || lower.includes('describ') || lower.includes('word')) {
    return totalTurns > 0 && completedTurns >= totalTurns ? 2 : 1;
  }
  if (lower.includes('draw') || lower.includes('lobby') || lower.includes('init') || lower.includes('prepare') || lower.includes('pending')) return 0;
  return totalTurns > 0 && completedTurns >= totalTurns ? 2 : 1;
}

export function nightSteps(phase: string, completedTurns: number, totalTurns: number, terminal: boolean): NightStep[] {
  const active = nightActiveStep(phase, completedTurns, totalTurns, terminal);
  return STEPS.map((step, index) => ({
    ...step,
    state: index < active ? 'done' : index === active ? 'active' : 'pending',
  }));
}

export function nightPhaseLabel(phase: string): string {
  const lower = phase.toLowerCase();
  if (lower.startsWith('pk_')) return lower.includes('vot') ? 'PK 投票' : 'PK 发言';
  if (lower.includes('vot')) return '投票';
  if (lower.includes('speak') || lower.includes('describ')) return '发言';
  if (lower.includes('discuss')) return '讨论';
  if (lower.includes('reveal')) return '终局';
  if (lower.includes('complete') || lower.includes('finish')) return '结算';
  if (lower.includes('lobby') || lower.includes('init') || lower.includes('prepare')) return '准备';
  return '进行中';
}

export interface NightDialogueEntry {
  actorId: string;
  kind: 'player' | 'host';
  displayName: string;
  seatNumber?: number;
  text: string;
  stage?: 'regular' | 'pk';
  source: 'output' | 'history' | 'broadcast';
}

function playerEntry(actor: ActorViewModel, text: string, source: NightDialogueEntry['source'], stage?: 'regular' | 'pk'): NightDialogueEntry {
  const player = actor.actor as { displayName: string; seatNumber: number };
  return {
    actorId: actor.actor.actorId,
    kind: 'player',
    displayName: player.displayName,
    seatNumber: player.seatNumber,
    text,
    stage,
    source,
  };
}

function hostEntry(actor: ActorViewModel | undefined, text: string): NightDialogueEntry | undefined {
  if (!actor) return undefined;
  return { actorId: actor.actor.actorId, kind: 'host', displayName: actor.actor.displayName, text, source: 'broadcast' };
}

function outputRecency(actor: ActorViewModel): number {
  const output = actor.latestOutput;
  if (!output) return -Infinity;
  return output.sequence ?? output.timestamp ?? -Infinity;
}

function latestHistoryEntry(model: UndercoverGameViewModel): NightDialogueEntry | undefined {
  const players = new Map(model.actors.filter((actor) => actor.kind === 'player').map((actor) => [actor.actor.actorId, actor]));
  for (let roundIndex = model.params.publicHistory.length - 1; roundIndex >= 0; roundIndex -= 1) {
    const round = model.params.publicHistory[roundIndex];
    for (let speechIndex = round.speeches.length - 1; speechIndex >= 0; speechIndex -= 1) {
      const speech = round.speeches[speechIndex];
      const actor = players.get(speech.actorId);
      if (!actor || !speech.text.trim()) continue;
      return playerEntry(actor, speech.text, 'history', round.stage ?? 'regular');
    }
  }
  return undefined;
}

/**
 * One authoritative public utterance for the bottom dialogue panel. Player
 * speech is hidden while votes are being collected; only the host's already
 * public broadcast is eligible then, matching the static vote page.
 */
export function nightDialogue(
  model: UndercoverGameViewModel,
  options: { voting: boolean; showHostOutput: boolean },
): NightDialogueEntry | undefined {
  const host = model.actors.find((actor) => actor.kind === 'host');
  if (options.voting) {
    if (!options.showHostOutput || !host?.latestOutput || isStructuredVote(host.latestOutput.text)) return undefined;
    return hostEntry(host, host.latestOutput.text);
  }
  const completed = model.actors
    .filter((actor) => actor.kind === 'player' && actor.latestOutput && !actor.latestOutput.pending)
    .sort((left, right) => outputRecency(right) - outputRecency(left));
  if (completed[0]) return playerEntry(completed[0], completed[0].latestOutput!.text, 'output');
  const history = latestHistoryEntry(model);
  if (history) return history;
  if (options.showHostOutput && host?.latestOutput) return hostEntry(host, host.latestOutput.text);
  return undefined;
}
