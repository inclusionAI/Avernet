import { latestAttemptEvents, mergePublicOutputEvents } from './currentRound';
import type {
  ActorViewModel,
  PendingHumanNode,
  PlayerActor,
  PlayerState,
  PublicOutputEvent,
  SessionMessage,
  StateMachineNode,
  StateMachineRunGraph,
  UndercoverGamePanelParams,
  UndercoverGameViewModel,
  VoteCandidate,
} from './types';

const TERMINAL = new Set(['completed', 'failed', 'aborted']);

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function stateMachineMetadata(message: SessionMessage): Record<string, unknown> | undefined {
  return record(record(message.metadata)?.state_machine);
}

function outputText(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  const object = record(value);
  return object && typeof object.text === 'string' ? object.text : undefined;
}

function compareEvents(left: PublicOutputEvent, right: PublicOutputEvent): number {
  return (left.sequence ?? Infinity) - (right.sequence ?? Infinity)
    || (left.timestamp ?? -Infinity) - (right.timestamp ?? -Infinity)
    || left.identity.localeCompare(right.identity);
}

export function mapPublicOutputEvents(
  messages: SessionMessage[],
  params: UndercoverGamePanelParams,
): PublicOutputEvent[] {
  const events = new Map<string, PublicOutputEvent>();
  const voting = params.phase.toLowerCase().includes('vot');
  for (const message of messages) {
    const metadata = stateMachineMetadata(message);
    if (!metadata || metadata.event !== 'output' || metadata.run_id !== params.runId
      || metadata.visibility === 'private' || metadata.public === false) continue;
    const nodeId = typeof metadata.node_id === 'string' ? metadata.node_id : '';
    const actorId = params.nodeActorMap[nodeId];
    const rawOutput = outputText(message.content)?.trim();
    if (!nodeId || !actorId || !rawOutput) continue;

    const isHost = actorId === params.host.actorId;
    if (voting && !isHost) continue;
    if (isHost && params.display?.showHostOutput === false) continue;

    const attempt = typeof metadata.attempt === 'number' && metadata.attempt > 0 ? metadata.attempt : 1;
    const identity = `${params.gameSessionId ?? 'phase'}:${params.runId}:${nodeId}:${attempt}`;
    const event: PublicOutputEvent = {
      identity,
      runId: params.runId,
      nodeId,
      attempt,
      actorId,
      text: rawOutput,
      pending: Boolean(metadata.pending ?? message.pending),
      sequence: typeof message.sequence === 'number' ? message.sequence : undefined,
      timestamp: typeof (message.created_at ?? message.timestamp) === 'number'
        ? message.created_at ?? message.timestamp
        : undefined,
      messageId: message.id ?? message.message_id,
      source: 'session-message',
    };
    const previous = events.get(identity);
    if (!previous || compareEvents(event, previous) >= 0) events.set(identity, event);
  }
  return latestAttemptEvents([...events.values()].sort(compareEvents));
}

function latestNode(nodes: StateMachineNode[], params: UndercoverGamePanelParams, actorId: string) {
  return nodes.filter((node) => params.nodeActorMap[node.node_id] === actorId)
    .reduce<StateMachineNode | undefined>((latest, node) => {
      if (!latest) return node;
      const latestTime = latest.completed_at ?? latest.started_at ?? -Infinity;
      const nodeTime = node.completed_at ?? node.started_at ?? -Infinity;
      if (nodeTime !== latestTime) return nodeTime > latestTime ? node : latest;
      return (node.attempt ?? 0) >= (latest.attempt ?? 0) ? node : latest;
    }, undefined);
}

function playerState(
  player: PlayerActor,
  node: StateMachineNode | undefined,
  pending: boolean,
  params: UndercoverGamePanelParams,
): PlayerState {
  if (player.eliminated || player.alive === false) return 'eliminated';
  if (player.state) return player.state;
  if (pending) return 'action_required';
  if (node?.status === 'running') return 'active_speech';
  if (node?.status === 'retry_scheduled' || ((node?.attempt ?? 1) > 1 && node?.status !== 'completed')) return 'retrying';
  if (node?.status === 'failed') return 'error';
  if (node?.status === 'completed') return params.phase.toLowerCase().includes('vot') ? 'voted' : 'completed_speech';
  return params.phase.toLowerCase().includes('vot') ? 'waiting_for_vote' : 'waiting';
}

export function normalizeUndercoverGameViewModel(
  params: UndercoverGamePanelParams,
  graph?: StateMachineRunGraph,
  pendingNodes: PendingHumanNode[] = [],
  messages: SessionMessage[] = [],
  resolvedEvents: PublicOutputEvent[] = [],
): UndercoverGameViewModel {
  const pendingHumanNode = pendingNodes.find((node) => Boolean(params.nodeActorMap[node.node_id]));
  const pendingActorId = pendingHumanNode ? params.nodeActorMap[pendingHumanNode.node_id] : undefined;
  const events = latestAttemptEvents(mergePublicOutputEvents(mapPublicOutputEvents(messages, params), resolvedEvents));
  const byActor = new Map<string, PublicOutputEvent[]>();
  for (const event of events) byActor.set(event.actorId, [...(byActor.get(event.actorId) ?? []), event]);

  const nodes = graph?.nodes ?? [];
  const players = new Map(params.players.map((player) => [player.actorId, player]));
  const historyByActor = new Map<string, typeof params.publicHistory[number]['speeches']>();
  for (const round of params.publicHistory) {
    for (const speech of round.speeches) {
      historyByActor.set(speech.actorId, [...(historyByActor.get(speech.actorId) ?? []), speech]);
    }
  }

  const actors: ActorViewModel[] = params.seatOrder.map((actorId, seatIndex) => {
    const actor = players.get(actorId)!;
    const node = latestNode(nodes, params, actorId);
    const outputHistory = byActor.get(actorId) ?? [];
    return {
      actor,
      kind: 'player',
      seatIndex,
      state: playerState(actor, node, pendingActorId === actorId, params),
      node,
      latestOutput: outputHistory[outputHistory.length - 1],
      outputHistory,
      publicHistory: historyByActor.get(actorId) ?? [],
    };
  });

  const hostHistory = byActor.get(params.host.actorId) ?? [];
  actors.push({
    actor: params.host,
    kind: 'host',
    state: 'host',
    node: latestNode(nodes, params, params.host.actorId),
    latestOutput: hostHistory[hostHistory.length - 1],
    outputHistory: hostHistory,
    publicHistory: [],
  });

  const completedTurns = params.turnOrder.filter((actorId) => latestNode(nodes, params, actorId)?.status === 'completed').length;
  const status = graph?.run.status ?? 'pending';
  return {
    params,
    run: graph?.run,
    status,
    terminal: TERMINAL.has(status),
    phase: params.phase,
    round: params.round,
    actors,
    pendingHumanNode,
    pendingHumanActorId: pendingActorId,
    publicEvents: events,
    completedTurns,
    totalTurns: params.turnOrder.length,
    lastUpdatedAt: graph?.run.updated_at,
  };
}

export function eligibleVoteCandidates(candidates: VoteCandidate[] = []): VoteCandidate[] {
  return candidates.filter((candidate) => candidate.eligible && !candidate.eliminated);
}

export { serializeVoteTarget as serializeVoteContent } from './actionContext';
