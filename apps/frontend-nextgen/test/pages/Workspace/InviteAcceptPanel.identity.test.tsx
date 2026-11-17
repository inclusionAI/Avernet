/** @jest-environment jsdom */
import { getCapabilities } from '@/capabilities';
import { InviteAcceptPanel } from '@/pages/Workspace/InviteAcceptPanel';
import { useSelectedGroupDetail } from '@/pages/Workspace/hooks/useSelectedGroupDetail';
import { parseWorkspaceRoute } from '@/domain/workspaceRoute';
import type { GroupView } from '@/domain/collaboration';
import { invitationService } from '@/services/workspace/invitationService';
import { groupService } from '@/services/workspace/groupService';
import { identityService } from '@/services/workspace/identityService';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { hydrateWorkspaceRoute } from '@/services/workspace/workspaceRouteService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

jest.mock('@/services/workspace/invitationService');
jest.mock('@/services/workspace/identityService');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/sessionService');
const user = { id: 'human_staff-1', kind: 'user' as const, displayName: '我', online: true };
const bot = { id: 'bot-1', kind: 'bot' as const, displayName: 'Bot', online: true };
const group: GroupView = {
  groupId: 'g1', name: 'G1', kind: 'free_chat', status: 'active', participants: [], sessions: [],
  lastMessageAt: 1, createdAt: 1, participantCount: 1, isPublic: false, deliveryPolicy: 'send_to_driver',
};

function Destination() {
  const { search } = useLocation();
  const activeIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const selectedGroupId = useWorkspaceStore((s) => s.selectedGroupId);
  const selectedSessionId = useWorkspaceStore((s) => s.selectedSessionId);
  useEffect(() => { hydrateWorkspaceRoute(parseWorkspaceRoute(search)); }, [search]);
  // Simulate the direct-membership sidebar page not containing a session-only parent Group.
  const selected = useSelectedGroupDetail(selectedGroupId, [], false, true);
  return <div data-testid="destination">{search}:{activeIdentityId}:{selected.group?.groupId}:{selectedSessionId}</div>;
}

afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
});

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([user, bot], bot.id);
  useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-1', displayName: '我', provider: 'github' });
  jest.mocked(groupService.loadGroupDetailOrBcs).mockResolvedValue({ ok: true, data: group });
});

it.each(['group', 'session'] as const)('a %s invitation explicitly selects its authenticated Human, not the active Bot', async (targetType) => {
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValue({ ok: true, data: {
    isValid: true, targetType, targetId: targetType === 'group' ? 'g1' : 's1', groupId: 'g1', alreadyJoined: true,
  } });
  render(
    <MemoryRouter initialEntries={[`/workspace/invite/${targetType}s/tk`]}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        <Route path="/workspace/collaboration" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
  const destination = await screen.findByTestId('destination');
  expect(destination).toHaveTextContent('?current=human_staff-1&group=g1');
  await waitFor(() => expect(useWorkspaceStore.getState().activeIdentityId).toBe(user.id));
  expect(useWorkspaceStore.getState().selectedSessionId).toBe(targetType === 'session' ? 's1' : null);
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});

it('a Session-only member can hydrate the target when the direct-membership sidebar omits its Group', async () => {
  jest.mocked(groupService.loadGroupDetailOrBcs).mockResolvedValue({ ok: true, data: { ...group, membership: 'session_only' } });
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValue({ ok: true, data: {
    isValid: true, targetType: 'session', targetId: 's1', groupId: 'g1', alreadyJoined: true,
  } });
  render(
    <MemoryRouter initialEntries={['/workspace/invite/sessions/tk']}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        <Route path="/workspace/collaboration" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
  const destination = await screen.findByTestId('destination');
  await waitFor(() => expect(destination).toHaveTextContent(':human_staff-1:g1:s1'));
  expect(groupService.loadGroupDetailOrBcs).toHaveBeenCalledWith('g1', undefined);
  expect(useWorkspaceStore.getState()).toMatchObject({ selectedGroupId: 'g1', selectedSessionId: 's1', membership: 'direct' });
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});


it('a late global authenticated User completes navigation without triggering mine/identity loading', async () => {
  useWorkspaceStore.getState().reset();
  useExternalAuthStore.getState().reset();
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValue({ ok: true, data: {
    isValid: true, targetType: 'group', targetId: 'g1', groupId: 'g1', alreadyJoined: true,
  } });
  render(
    <MemoryRouter initialEntries={['/workspace/invite/groups/tk']}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        <Route path="/workspace/collaboration" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText('正在打开协作群…')).toBeInTheDocument();
  expect(screen.queryByTestId('destination')).not.toBeInTheDocument();
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  act(() => useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-1', displayName: '我', provider: 'github' }));
  const destination = await screen.findByTestId('destination');
  expect(destination).toHaveTextContent('?current=human_staff-1&group=g1');
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});

it.each(['group', 'session'] as const)('an already joined %s waits for gateway identity without flashing an error or confirmation', async (targetType) => {
  useWorkspaceStore.getState().reset();
  useExternalAuthStore.getState().reset();
  jest.spyOn(getCapabilities(), 'getLoginStrategy').mockReturnValue({ status: 'available', value: 'ace-gateway' });
  const identity = jest.spyOn(getCapabilities(), 'getHumanIdentity').mockReturnValue({ status: 'available', value: null });
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValue({ ok: true, data: {
    isValid: true, targetType, targetId: targetType === 'group' ? 'g1' : 's1', groupId: 'g1', alreadyJoined: true,
  } });
  render(
    <MemoryRouter initialEntries={[`/workspace/invite/${targetType === 'group' ? 'groups' : 'sessions'}/tk`]}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        <Route path="/workspace/collaboration" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText(targetType === 'session' ? '正在打开会话…' : '正在打开协作群…')).toBeInTheDocument();
  expect(screen.queryByText('当前用户身份未就绪')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认加入' })).not.toBeInTheDocument();
  expect(screen.queryByTestId('destination')).not.toBeInTheDocument();
  identity.mockReturnValue({ status: 'available', value: { userId: 'staff-1', displayName: '我', online: true } });
  act(() => useWorkspaceStore.getState().setIdentities([user, bot], bot.id));
  const destination = await screen.findByTestId('destination');
  expect(destination).toHaveTextContent(`?current=human_staff-1&group=g1${targetType === 'session' ? '&session=s1' : ''}`);
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
});

it('shows a refresh error instead of spinning forever when gateway identity never initializes', async () => {
  jest.useFakeTimers();
  useWorkspaceStore.getState().reset();
  useExternalAuthStore.getState().reset();
  jest.spyOn(getCapabilities(), 'getLoginStrategy').mockReturnValue({ status: 'available', value: 'ace-gateway' });
  jest.spyOn(getCapabilities(), 'getHumanIdentity').mockReturnValue({ status: 'available', value: null });
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValue({ ok: true, data: {
    isValid: true, targetType: 'session', targetId: 's1', groupId: 'g1', alreadyJoined: true,
  } });
  render(
    <MemoryRouter initialEntries={['/workspace/invite/sessions/tk']}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        <Route path="/workspace/collaboration" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  expect(screen.getByText('正在打开会话…')).toBeInTheDocument();
  act(() => jest.advanceTimersByTime(10_000));
  expect(screen.getByText('当前用户身份加载超时，请刷新页面后重试。')).toBeInTheDocument();
  expect(screen.queryByText('正在打开会话…')).not.toBeInTheDocument();
  expect(screen.queryByTestId('destination')).not.toBeInTheDocument();
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});
