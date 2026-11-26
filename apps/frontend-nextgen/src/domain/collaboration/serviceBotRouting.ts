export type ServiceBotStage = 'online' | 'verify' | 'draft';
export type ServiceBotRoutingMode = 'dynamic' | 'specified';
export type ParticipantRoutingState =
  | { status: 'loading' | 'error' | 'non_service' }
  | { status: 'service'; routingMode: ServiceBotRoutingMode; stage?: ServiceBotStage };
export type ParticipantRoutingStates = Record<string, ParticipantRoutingState>;

export const SERVICE_BOT_STAGE_OPTIONS: Array<{ value: ServiceBotStage; label: string }> = [
  { value: 'online', label: '生产' },
  { value: 'verify', label: '预发' },
  { value: 'draft', label: '草稿' },
];

export function areParticipantRoutesReady(actorIds: string[], states: ParticipantRoutingStates): boolean {
  return actorIds.every((id) => {
    const state = states[id];
    return (
      state?.status === 'non_service' ||
      (state?.status === 'service' && (state.routingMode === 'dynamic' || Boolean(state.stage)))
    );
  });
}

export function attachParticipantStageTags<T extends { actor_id: string }>(
  participants: T[],
  states: ParticipantRoutingStates,
): Array<T & { tags?: ServiceBotStage[] }> {
  return participants.map((participant) => {
    const state = states[participant.actor_id];
    return state?.status === 'service' && state.routingMode === 'specified' && state.stage
      ? { ...participant, tags: [state.stage] }
      : participant;
  });
}
