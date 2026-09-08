/** @jest-environment jsdom */
import type { GroupView, SessionView } from '@/domain/collaboration';
import { FuseSlot } from '@/pages/Workspace/components/GroupChatPane/FuseSlot';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockUseFuseCalls: any[][] = [];

jest.mock('@/pages/Workspace/hooks/useFuse', () => ({
  useFuse: (...args: unknown[]) => {
    mockUseFuseCalls.push(args);
    return {
      messages: [],
      isFusing: false,
      submitQuestion: () => {},
      clearSessionMessages: () => {},
      fusionBots: [{ botUuid: 'bot-driver', name: 'Driver Bot', fusionEnable: true }],
      isLoadingFusionBots: false,
    };
  },
}));

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

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '测试会话',
  kind: 'chat',
  status: 'running',
  participants: [
    { actorId: 'bot-driver', kind: 'bot', name: 'Driver Bot', role: 'driver', mode: 'auto' },
    { actorId: 'bot-member', kind: 'bot', name: 'Member Bot', role: 'member', mode: 'auto' },
  ],
  lastMessageAt: 1,
  createdAt: 1,
  favorite: false,
};

beforeEach(() => {
  mockUseFuseCalls = [];
});

it('passes session participants to fusion config loading after clicking the button', async () => {
  render(<FuseSlot group={group} session={session} sessionId={session.sessionId} viewerName="章梧" />);

  expect(mockUseFuseCalls.at(-1)?.[0]).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /融合模式/ }));

  await waitFor(() => {
    expect(mockUseFuseCalls.at(-1)).toEqual([group, 's1', session.participants]);
  });
});
