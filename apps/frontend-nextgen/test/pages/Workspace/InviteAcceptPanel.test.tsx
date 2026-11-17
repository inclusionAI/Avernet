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
  return <div data-testid="destination">{location.search}:{navigationType}</div>;
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
    jest.mocked(sessionService.getSessionDetail).mockReset();
  });

  it('renders confirm view when invitation is valid and posts accept on confirm', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
    svc.acceptInvitation.mockResolvedValue({
      ok: true,
      data: { targetType: 'group', targetId: 'g1', alreadyJoined: false },
    });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/groups/tk-1']}>
        <Routes>
          <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/是否确认加入/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    await waitFor(() => expect(svc.acceptInvitation).toHaveBeenCalled());
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
    expect(destination).toHaveTextContent(targetType === 'group' ? '?current=human_staff-1&group=g1:REPLACE' : '?current=human_staff-1&group=g1&session=s1:REPLACE');
    expect(screen.queryByRole('button', { name: '确认加入' })).not.toBeInTheDocument();
    expect(svc.acceptInvitation).not.toHaveBeenCalled();
    expect(sessionService.getSessionDetail).not.toHaveBeenCalled();
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
