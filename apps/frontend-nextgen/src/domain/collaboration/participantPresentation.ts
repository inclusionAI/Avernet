import type { ParticipantSummaryView, ParticipantView } from './types';
export function getDisplayParticipants(value: {
  participants: ParticipantView[];
  participantSummaries?: ParticipantSummaryView[];
}): ParticipantSummaryView[] {
  return value.participantSummaries ?? value.participants;
}
export function getParticipantDisplayCount(
  count: number | undefined, participants: readonly ParticipantSummaryView[],
): number {
  return typeof count === 'number' && Number.isFinite(count) && count >= 0
    ? count : participants.length;
}
