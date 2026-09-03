import type {
  ActorViewModel,
  HostActor,
  PendingHumanNode,
  PlayerActor,
  PublicOutputEvent,
  SessionMessage,
  StateMachineNode,
  StateMachineRunGraph,
  UndercoverGamePanelParams,
  UndercoverGameViewModel,
  VoteCandidate,
  PlayerState,
} from './types';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'aborted']);

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function stateMachineMetadata(message: SessionMessage): Record<string, unknown> | undefined {
  return asRecord(asRecord(message.metadata)?.state_machine);
}

function outputText(content: unknown): string | undefined {
  if (typeof content === 'string') return content;
  const record = asRecord(content);
  if (record && typeof record.text === 'string') return record.text;
  return undefined;
}

function eventTimestamp(message: SessionMessage): number | undefined {
  const value = message.created_at ?? message.timestamp;
  return typeof value === 'number' ? value : undefined;
}

function eventIdentity(runId: string, gameSessionId: string | undefined, nodeId: string, attempt: number): string {
  return `${gameSessionId ?? 'phase'}:${runId}:${nodeId}:${attempt}`;
}

function compareEvents(left: PublicOutputEvent, right: PublicOutputEvent): number {
  if (left.sequence !== undefined || right.sequence !== undefined) {
    return (left.sequence ?? Number.NEGATIVE_INFINITY) - (right.sequence ?? Number.NEGATIVE_INFINITY);
  }
  if (left.timestamp !== undefined || right.timestamp !== undefined) {
    return (left.timestamp ?? Number.NEGATIVE_INFINITY) - (right.timestamp ?? Number.NEGATIVE_INFINITY);
  }
  return left.identity.localeCompare(right.identity);
}

/**
 * Extract only state-machine-tagged output events. No natural-language output
 * is inspected for phase, vote, identity, role, word, or elimination facts.
 */
export function mapPublicOutputEvents(
  messages: SessionMessage[],
  params: UndercoverGamePanelParams,
): PublicOutputEvent[] {
  const byIdentity = new Map<string, PublicOutputEvent>();
  for (const message of messages) {
    const metadata = stateMachineMetadata(message);
    if (
      !metadata ||
      metadata.event !== 'output' ||
      metadata.run_id !== params.runId ||
      metadata.visibility === 'private' ||
      metadata.public === false
    ) continue;
    const nodeId = typeof metadata.node_id === 'string' ? metadata.node_id : '';
    const actorId = nodeId ? params.nodeActorMap[nodeId] : undefined;
    const text = outputText(message.content);
    if (!nodeId || !actorId || !text) continue;
    const attempt = typeof metadata.attempt === 'number' && metadata.attempt > 0 ? metadata.attempt : 1;
    const event: PublicOutputEvent = {
      identity: eventIdentity(params.runId, params.gameSessionId, nodeId, attempt),
      runId: params.runId,
      nodeId,
      attempt,
      actorId,
      text,
      pending: Boolean(metadata.pending ?? message.pending),
      sequence: typeof message.sequence === 'number' ? message.sequence : undefined,
      timestamp: eventTimestamp(message),
      messageId: message.id ?? message.message_id,
    };
    const previous = byIdentity.get(event.identity);
    if (!previous || compareEvents(event, previous) >= 0) byIdentity.set(event.identity, event);
  }
  return [...byIdentity.values()].sort(compareEvents);
}

function nodesForActor(nodes: StateMachineNode[], params: UndercoverGamePanelParams, actorId: string): StateMachineNode[] {
  return nodes.filter((node) => params.nodeActorMap[node.node_id] === actorId);
}

function latestNode(nodes: StateMachineNode[], params: UndercoverGamePanelParams, actorId: string): StateMachineNode | undefined {
  return nodesForActor(nodes, params, actorId).reduce<StateMachineNode | undefined>((latest, node) => {
    if (!latest) return node;
    const latestTime = latest.completed_at ?? latest.started_at ?? Number.NEGATIVE_INFINITY;
    const nodeTime = node.completed_at ?? node.started_at ?? Number.NEGATIVE_INFINITY;
    if (nodeTime !== latestTime) return nodeTime > latestTime ? node : latest;
    const latestAttempt = latest.attempt ?? 0;
    const nodeAttempt = node.attempt ?? 0;
    if (nodeAttempt !== latestAttempt) return nodeAttempt > latestAttempt ? node : latest;
    return node.node_id.localeCompare(latest.node_id) > 0 ? node : latest;
  }, undefined);
}

function playerState(
  player: PlayerActor,
  nodes: StateMachineNode[],
  pendingHumanNode: PendingHumanNode | undefined,
  params: UndercoverGamePanelParams,
): PlayerState {
  if (player.eliminated === true || player.alive === false || player.state === 'eliminated') return 'eliminated';
  if (player.state) return player.state;
  if (player.voted === true) return 'voted';
  if (pendingHumanNode && params.nodeActorMap[pendingHumanNode.node_id] === player.actorId) {
    return params.phase.toLowerCase().includes('vot') ? 'waiting_for_vote' : 'waiting';
  }
  const latest = latestNode(nodes, params, player.actorId);
  if (latest?.status === 'running') return 'active_speech';
  if (latest?.status === 'retry_scheduled') return 'retrying';
  if (latest?.status === 'failed') return 'error';
  if (latest?.status === 'completed') {
    return params.phase.toLowerCase().includes('vot') ? 'voted' : 'completed_speech';
  }
  return 'waiting';
}

export function normalizeUndercoverGameViewModel(
  params: UndercoverGamePanelParams,
  graph?: StateMachineRunGraph,
  pendingHumanNodes: PendingHumanNode[] = [],
  messages: SessionMessage[] = [],
): UndercoverGameViewModel {
  const pendingHumanNode = pendingHumanNodes.length === 1 ? pendingHumanNodes[0] : undefined;
  const publicEvents = mapPublicOutputEvents(messages, params);
  const outputsByActor = new Map<string, PublicOutputEvent[]>();
  for (const event of publicEvents) {
    const outputs = outputsByActor.get(event.actorId) ?? [];
    outputs.push(event);
    outputsByActor.set(event.actorId, outputs);
  }
  const nodes = graph?.nodes ?? [];
  const playerMap = new Map(params.players.map((player) => [player.actorId, player]));
  const actors: ActorViewModel[] = params.seatOrder.map((actorId, seatIndex) => {
    const player = playerMap.get(actorId) as PlayerActor;
    const history = outputsByActor.get(actorId) ?? [];
    return {
      actor: player,
      kind: 'player',
      seatIndex,
      state: playerState(player, nodes, pendingHumanNode, params),
      node: latestNode(nodes, params, actorId),
      latestOutput: history[history.length - 1],
      outputHistory: history,
    };
  });
  const hostHistory = outputsByActor.get(params.host.actorId) ?? [];
  actors.push({
    actor: params.host,
    kind: 'host',
    state: 'host',
    node: latestNode(nodes, params, params.host.actorId),
    latestOutput: hostHistory[hostHistory.length - 1],
    outputHistory: hostHistory,
  });
  const status = graph?.run.status ?? 'pending';
  return {
    params,
    run: graph?.run,
    status,
    terminal: TERMINAL_STATUSES.has(status),
    phase: params.phase,
    round: params.round,
    actors,
    pendingHumanNode,
    pendingHumanActorId: pendingHumanNode ? params.nodeActorMap[pendingHumanNode.node_id] : undefined,
    publicEvents,
    lastUpdatedAt: graph?.run.updated_at,
  };
}

export function eligibleVoteCandidates(candidates: VoteCandidate[] = []): VoteCandidate[] {
  return candidates.filter((candidate) => candidate.eligible === true && candidate.eliminated !== true);
}

export function serializeVoteContent(targetActorId: string): string {
  if (!targetActorId.trim()) throw new Error('A vote target is required.');
  return JSON.stringify({ kind: 'vote', target_actor_id: targetActorId.trim() });
}
