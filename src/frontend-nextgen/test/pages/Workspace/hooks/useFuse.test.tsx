/** @jest-environment jsdom */
import type { GroupView, SessionView } from '@/domain/collaboration';
import { useFuse } from '@/pages/Workspace/hooks/useFuse';
import { bcsfuseService } from '@/services/workspace/bcsfuseService';
import { useFuseStore } from '@/stores/fuseStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/bcsfuseService');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const service = bcsfuseService as unknown as Record<string, jest.Mock<any>>;

const group: GroupView = {
  groupId: 'g1',
  name: '测试群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 2,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const sessionParticipants: SessionView['participants'] = [
  { actorId: 'bot-driver', kind: 'bot', name: 'Driver Bot', role: 'driver', mode: 'auto' },
  { actorId: 'bot-member', kind: 'bot', name: 'Member Bot', role: 'member', mode: 'auto' },
  { actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' },
];

beforeEach(() => {
  jest.resetAllMocks();
  useFuseStore.getState().reset();
  service.getFusionBots.mockResolvedValue({
    ok: true,
    data: [
      { botUuid: 'bot-driver', name: 'Driver Bot', fusionEnable: true },
      { botUuid: 'bot-member', name: 'Member Bot', fusionEnable: true },
    ],
  });
  service.postFuse.mockResolvedValue({ ok: true, data: { summary: '回答', success: true } });
});

it('does not fetch fusion bots before the panel is opened', () => {
  renderHook(() => useFuse(null, 's1', sessionParticipants));
  expect(service.getFusionBots).not.toHaveBeenCalled();
});

it('fetches fusion bots from session participants after the panel opens', async () => {
  renderHook(() => useFuse(group, 's1', sessionParticipants));
  await waitFor(() => expect(service.getFusionBots).toHaveBeenCalledWith(sessionParticipants));
});

it('uses the session driver when posting a fuse question', async () => {
  const { result } = renderHook(() => useFuse(group, 's1', sessionParticipants));
  await waitFor(() => expect(result.current.fusionBots).toHaveLength(2));

  await act(async () => {
    await result.current.submitQuestion('问题', ['bot-member']);
  });

  expect(service.postFuse).toHaveBeenCalledWith('g1', {
    session_id: 's1',
    question: '问题',
    driver_bot_id: 'bot-driver',
    participants: ['bot-member'],
    fusion_mode: 'bot_profile_fuse',
    options: { timeout_ms: 180000 },
  });
});
