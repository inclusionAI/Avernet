/** @jest-environment jsdom */
import { InviteAcceptPanel } from '@/pages/Workspace/InviteAcceptPanel';
import { invitationService } from '@/services/workspace/invitationService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from 'react-router-dom';
import { sessionService } from '@/services/workspace/sessionService';

jest.mock('@/services/workspace/invitationService');
jest.mock('@/services/workspace/sessionService');

function Destination() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return <div data-testid="destination">{location.pathname}{location.search}:{navigationType}</div>;
}

function renderInvite(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        <Route path="/workspace/collaboration/*" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
}

const svc = invitationService as unknown as {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  getAcceptPageState: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  acceptInvitation: any;
};

describe('InviteAcceptPanel', () => {
  beforeEach(() => {
    useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-1', displayName: '我', provider: 'github' });
    svc.getAcceptPageState.mockReset();
    svc.acceptInvitation.mockReset();
    jest.mocked(sessionService.getSessionDetail).mockReset().mockResolvedValue({
      ok: false,
      error: { code: 'SESSION_NOT_FOUND', friendlyMessage: '未找到会话', canRetry: false },
    });
  });

  it('renders confirm view when invitation is valid and posts accept on confirm', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
    svc.acceptInvitation.mockResolvedValue({
      ok: true,
      data: { targetType: 'group', targetId: 'g1', alreadyJoined: false },
    });
    renderInvite('/workspace/invite/groups/tk-1');
    expect(await screen.findByText(/是否确认加入/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    await waitFor(() => expect(svc.acceptInvitation).toHaveBeenCalled());
    expect((await screen.findByTestId('destination')).textContent).toBe(
      '/workspace/collaboration?current=human_staff-1&group=g1:REPLACE',
    );
  });

  it('uses sessions path segment to show session join copy', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/sessions/tk-9']}>
        <Routes>
          <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/是否确认加入该会话/)).toBeInTheDocument();
    expect(screen.getByText(/参与该会话/)).toBeInTheDocument();
  });

  it('shows Empty with invalid reason for 410-style failure', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
    svc.acceptInvitation.mockResolvedValue({
      ok: false,
      error: { code: 'INVITATION_INVALID', friendlyMessage: '该邀请已失效，请让群主重新生成。' },
    });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/groups/bad']}>
        <Routes>
          <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: '确认加入' }));
    expect(await screen.findByText('该邀请已失效，请让群主重新生成。')).toBeInTheDocument();
  });

  it.each(['group', 'session'] as const)('already-joined %s navigates immediately and replaces the invite history entry', async (targetType) => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: {
      isValid: true, targetType, targetId: targetType === 'group' ? 'g1' : 's1', groupId: 'g1', alreadyJoined: true,
    } });
    render(
      <MemoryRouter initialEntries={[`/workspace/invite/${targetType}s/tk-1`]}>
        <Routes>
          <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
          <Route path="/workspace/collaboration" element={<Destination />} />
        </Routes>
      </MemoryRouter>,
    );
    const destination = await screen.findByTestId('destination');
    const session = targetType === 'session' ? '&session=s1' : '';
    expect(destination).toHaveTextContent(`/workspace/collaboration?current=human_staff-1&group=g1${session}:REPLACE`);
    expect(screen.queryByRole('button', { name: '确认加入' })).not.toBeInTheDocument();
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
    expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
  });

  it.each([
    { type: 'group', search: 'view=group', suffix: '/group', returnParam: '' },
    { type: 'group', search: 'view=group&showReturn=false', suffix: '/group', returnParam: '&showReturn=false' },
    { type: 'group', search: 'view=full&showReturn=false', suffix: '', returnParam: '' },
    { type: 'group', search: 'view=session', suffix: '', returnParam: '' },
    { type: 'session', search: 'view=session&showReturn=false', suffix: '/session', returnParam: '&showReturn=false' },
    { type: 'session', search: 'view=group&showReturn=true', suffix: '/group', returnParam: '&showReturn=true' },
    { type: 'session', search: 'view=full', suffix: '', returnParam: '' },
    { type: 'session', search: 'showReturn=false', suffix: '', returnParam: '' },
    { type: 'session', search: 'view=unknown', suffix: '', returnParam: '' },
    { type: 'session', search: 'view=session&showReturn=0', suffix: '/session', returnParam: '' },
  ])('an existing $type member follows ?$search', async ({ type, search, suffix, returnParam }) => {
    const targetId = type === 'group' ? 'g1' : 'g1:s1';
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: {
      isValid: true, targetType: type, targetId, groupId: 'g1', alreadyJoined: true,
    } });
    renderInvite(`/workspace/invite/${type}s/tk-1?${search}`);
    const session = type === 'session' ? '&session=g1%3As1' : '';
    expect((await screen.findByTestId('destination')).textContent).toBe(
      `/workspace/collaboration${suffix}?current=human_staff-1&group=g1${session}${returnParam}:REPLACE`,
    );
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
  });

  it.each([
    { type: 'group', pathType: 'sessions', view: 'group' },
    { type: 'session', pathType: 'sessions', view: 'group' },
    { type: 'session', pathType: 'sessions', view: 'session' },
  ])('after confirming a $type invite, opens $view using the verified target', async ({ type, pathType, view }) => {
    const targetId = type === 'group' ? 'g1' : 'g1:s1';
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: {
      isValid: true, targetType: type, targetId, groupId: 'g1', alreadyJoined: false,
    } });
    svc.acceptInvitation.mockResolvedValue({ ok: true, data: { targetType: type, targetId, alreadyJoined: false } });
    renderInvite(`/workspace/invite/${pathType}/tk-1?view=${view}&showReturn=false&group=other`);
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: '确认加入' }));
    const session = type === 'session' ? '&session=g1%3As1' : '';
    expect((await screen.findByTestId('destination')).textContent).toBe(
      `/workspace/collaboration/${view}?current=human_staff-1&group=g1${session}&showReturn=false:REPLACE`,
    );
    expect(svc.acceptInvitation).toHaveBeenCalledTimes(1);
    expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
  });

  it('does not use a different preview target to build a focused link with no verified parent group', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: {
      isValid: true, targetType: 'session', targetId: 'g1:s1', groupId: 'g1', alreadyJoined: false,
    } });
    svc.acceptInvitation.mockResolvedValue({
      ok: true, data: { targetType: 'session', targetId: 'g2:s2', alreadyJoined: false },
    });
    renderInvite('/workspace/invite/sessions/tk-1?view=session');
    fireEvent.click(await screen.findByRole('button', { name: '确认加入' }));
    expect((await screen.findByTestId('destination')).textContent).toBe(
      '/workspace/collaboration?current=human_staff-1&session=g2%3As2:REPLACE',
    );
    expect(sessionService.getSessionDetail).toHaveBeenCalledWith('g2:s2');
  });

  it('preview service failure renders retry instead of a confirmation button', async () => {
    svc.getAcceptPageState.mockResolvedValueOnce({ ok: false, error: {
      code: 'INVITATION_PREVIEW_FAILED', friendlyMessage: '邀请校验失败，请稍后重试。', canRetry: true,
    } });
    svc.getAcceptPageState.mockResolvedValueOnce({ ok: true, data: {
      isValid: true, targetType: 'group', targetId: 'g1', groupId: 'g1', alreadyJoined: false,
    } });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/groups/tk-1']}>
        <Routes><Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('邀请校验失败，请稍后重试。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认加入' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新校验' }));
    expect(await screen.findByRole('button', { name: '确认加入' })).toBeInTheDocument();
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
  });


  it('authentication preflight failure does not offer confirmation or transient retry', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: false, error: {
      code: 'INVITATION_UNAUTHENTICATED', friendlyMessage: '请登录后再查看协作邀请。', canRetry: false,
    } });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/groups/tk-1']}>
        <Routes><Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('请登录后再查看协作邀请。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认加入' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新校验' })).not.toBeInTheDocument();
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
  });


  it('uses the verified target type instead of an edited URL path for confirmation copy', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: {
      isValid: true, targetType: 'session', targetId: 's1', groupId: 'g1', alreadyJoined: false,
    } });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/groups/session-token']}>
        <Routes><Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('是否确认加入该会话')).toBeInTheDocument();
    expect(screen.queryByText('是否确认加入该协作群')).not.toBeInTheDocument();
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
  });

});
