import type { UndercoverGamePanelParams, UndercoverGamePanelProps, PlayerActor } from './types';

// Browser panels are served by the frontend, so use its BCS proxy by default.
// Direct /api/v1 routes target the management gateway and are unavailable in
// standalone BCN deployments.
export const DEFAULT_API_BASE_URL = '/bcnproxy';
export const DEFAULT_POLLING_INTERVAL = 3000;
export const DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024;

export class PanelParamsError extends Error {
  readonly code = 'INVALID_PANEL_PARAMS';

  constructor(message: string) {
    super(message);
    this.name = 'PanelParamsError';
  }
}

function mergeParams(props: UndercoverGamePanelProps): Record<string, unknown> {
  const data = props.data && typeof props.data === 'object' ? props.data : {};
  return { ...data, ...props } as Record<string, unknown>;
}

function normalizePlayers(value: unknown): PlayerActor[] {
  if (Array.isArray(value)) {
    return value as PlayerActor[];
  }
  if (value && typeof value === 'object') {
    return Object.values(value as Record<string, PlayerActor>);
  }
  return [];
}

function requireText(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new PanelParamsError(`${field} is required.`);
  }
  return value.trim();
}

export function normalizePanelParams(props: UndercoverGamePanelProps): UndercoverGamePanelParams {
  const raw = mergeParams(props);
  const runId = requireText(raw.runId ?? raw.stateMachineRunId ?? raw.smRunId, 'runId');
  const groupId = requireText(raw.groupId, 'groupId');
  const sessionId = requireText(raw.sessionId, 'sessionId');
  const phase = requireText(raw.phase, 'phase');
  const round = typeof raw.round === 'number' && Number.isFinite(raw.round) && raw.round >= 0 ? raw.round : NaN;
  if (!Number.isFinite(round)) {
    throw new PanelParamsError('round must be a finite non-negative number.');
  }

  const hostValue = raw.host;
  if (!hostValue || typeof hostValue !== 'object') {
    throw new PanelParamsError('host is required.');
  }
  const hostObject = hostValue as Record<string, unknown>;
  const host = {
    actorId: requireText(hostObject.actorId, 'host.actorId'),
    displayName: requireText(hostObject.displayName, 'host.displayName'),
    subtitle: typeof hostObject.subtitle === 'string' ? hostObject.subtitle : undefined,
    avatar: typeof hostObject.avatar === 'string' ? hostObject.avatar : undefined,
  };

  if (!Array.isArray(raw.seatOrder) || raw.seatOrder.length === 0) {
    throw new PanelParamsError('seatOrder must contain at least one player actor.');
  }
  const seatOrder = raw.seatOrder.map((actorId) => requireText(actorId, 'seatOrder actorId'));
  if (new Set(seatOrder).size !== seatOrder.length) {
    throw new PanelParamsError('seatOrder must not contain duplicate actor IDs.');
  }

  const players = normalizePlayers(raw.players);
  const playerMap = new Map(players.map((player) => [player.actorId, player]));
  for (const actorId of seatOrder) {
    const player = playerMap.get(actorId);
    if (!player || !player.displayName) {
      throw new PanelParamsError(`players must define public data for seat ${actorId}.`);
    }
  }

  if (!raw.nodeActorMap || typeof raw.nodeActorMap !== 'object' || Array.isArray(raw.nodeActorMap)) {
    throw new PanelParamsError('nodeActorMap is required and must be an object.');
  }
  const nodeActorMap = Object.fromEntries(
    Object.entries(raw.nodeActorMap as Record<string, unknown>).map(([nodeId, actorId]) => [
      requireText(nodeId, 'nodeActorMap nodeId'),
      requireText(actorId, `nodeActorMap.${nodeId}`),
    ]),
  );

  const currentActionValue = raw.currentAction;
  const currentAction = currentActionValue && typeof currentActionValue === 'object' && !Array.isArray(currentActionValue)
    ? {
        actorId: requireText((currentActionValue as Record<string, unknown>).actorId, 'currentAction.actorId'),
        type: requireText((currentActionValue as Record<string, unknown>).type, 'currentAction.type'),
        nodeId: requireText((currentActionValue as Record<string, unknown>).nodeId, 'currentAction.nodeId'),
        deadlineAt: typeof (currentActionValue as Record<string, unknown>).deadlineAt === 'number'
          ? (currentActionValue as Record<string, unknown>).deadlineAt as number
          : undefined,
      }
    : undefined;

  return {
    runId,
    groupId,
    sessionId,
    gameSessionId: typeof raw.gameSessionId === 'string' && raw.gameSessionId.trim() ? raw.gameSessionId.trim() : undefined,
    phase,
    round,
    deadlineAt: typeof raw.deadlineAt === 'number' ? raw.deadlineAt : undefined,
    host,
    seatOrder,
    players,
    nodeActorMap,
    voteCandidates: Array.isArray(raw.voteCandidates) ? raw.voteCandidates as UndercoverGamePanelParams['voteCandidates'] : [],
    apiBaseUrl: typeof raw.apiBaseUrl === 'string' && raw.apiBaseUrl.trim() ? raw.apiBaseUrl.trim() : DEFAULT_API_BASE_URL,
    currentViewerActorId: typeof raw.currentViewerActorId === 'string' ? raw.currentViewerActorId : undefined,
    currentAction,
    display: raw.display && typeof raw.display === 'object' ? raw.display as UndercoverGamePanelParams['display'] : {},
    pollingInterval: typeof raw.pollingInterval === 'number' && raw.pollingInterval > 0 ? raw.pollingInterval : DEFAULT_POLLING_INTERVAL,
    autoRefresh: raw.autoRefresh !== false,
    maxResponseBytes: typeof raw.maxResponseBytes === 'number' && raw.maxResponseBytes > 0 ? raw.maxResponseBytes : DEFAULT_MAX_RESPONSE_BYTES,
  };
}
