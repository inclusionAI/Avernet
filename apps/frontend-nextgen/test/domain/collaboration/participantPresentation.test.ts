import { getDisplayParticipants, getParticipantDisplayCount } from '@/domain/collaboration/participantPresentation';
import type { ParticipantView } from '@/domain/collaboration';

const old: ParticipantView = { actorId: 'b1', kind: 'bot', name: 'A',
  role: 'driver', mode: 'auto', avatarUrl: '/old.png' };
test('explicit empty summary clears stale detail display', () => {
  expect(getDisplayParticipants({ participants: [old], participantSummaries: [] })).toEqual([]);
});
test('legacy missing summary can display detail', () => {
  expect(getDisplayParticipants({ participants: [old] })).toEqual([old]);
});
test('zero is a valid count; absent count uses visible members', () => {
  expect(getParticipantDisplayCount(0, [old])).toBe(0);
  expect(getParticipantDisplayCount(undefined, [old])).toBe(1);
});
