/** @jest-environment jsdom */
import { InviteAcceptPanel } from '@/pages/Workspace/InviteAcceptPanel';
import { useInviteAccept } from '@/pages/Workspace/hooks/useInviteAccept';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

jest.mock('@/pages/Workspace/hooks/useInviteAccept');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const hook = useInviteAccept as unknown as jest.Mock<any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let acceptMock: jest.Mock<any>;

function setupConfirmState() {
  acceptMock = jest.fn<any>().mockResolvedValue(undefined);
  hook.mockReturnValue({ status: 'confirm', accept: acceptMock, resetToConfirm: jest.fn() });
}

function renderPanel() {
  setupConfirmState();
  return render(
    <MemoryRouter initialEntries={['/workspace/invite/groups/tk']}>
      <Routes>
        <Route path="/workspace/invite/:type/:token" element={<InviteAcceptPanel />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('InviteAcceptPanel 视角选择', () => {
  it('确认页展示下拉框，默认完整视角；选参与者视角后确认加入传 scope', () => {
    renderPanel();
    const scopeSelect = screen.getByRole('button', { name: '消息视角' });
    expect(scopeSelect).toHaveTextContent('完整视角');
    fireEvent.click(scopeSelect);
    fireEvent.click(screen.getByRole('option', { name: /参与者视角/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认加入' }));
    expect(acceptMock).toHaveBeenCalledWith('participant');
  });
});
