/** @jest-environment jsdom */
import { InviteAcceptPanel } from '@/pages/Workspace/InviteAcceptPanel';
import { invitationService } from '@/services/workspace/invitationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

jest.mock('@/services/workspace/invitationService');

const svc = invitationService as unknown as {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  getAcceptPageState: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  acceptInvitation: any;
};

describe('InviteAcceptPanel', () => {
  beforeEach(() => {
    svc.getAcceptPageState.mockReset();
    svc.acceptInvitation.mockReset();
  });

  it('renders confirm view when invitation is valid and posts accept on confirm', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
    svc.acceptInvitation.mockResolvedValue({
      ok: true,
      data: { targetType: 'group', targetId: 'g1', alreadyJoined: false },
    });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/tk-1']}>
        <Routes>
          <Route path="/workspace/invite/:token" element={<InviteAcceptPanel />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/是否确认加入/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    await waitFor(() => expect(svc.acceptInvitation).toHaveBeenCalledWith('tk-1'));
  });

  it('uses type=session to show session join copy', async () => {
    svc.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
    render(
      <MemoryRouter initialEntries={['/workspace/invite/tk-9?type=session']}>
        <Routes>
          <Route path="/workspace/invite/:token" element={<InviteAcceptPanel />} />
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
      <MemoryRouter initialEntries={['/workspace/invite/bad']}>
        <Routes>
          <Route path="/workspace/invite/:token" element={<InviteAcceptPanel />} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: '确认加入' }));
    expect(await screen.findByText('该邀请已失效，请让群主重新生成。')).toBeInTheDocument();
  });
});
