/** @jest-environment jsdom */
import { AddMemberDialog } from '@/pages/Workspace/components/ManagePanel/AddMemberDialog';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/collaborationCandidateService');

const cs = collaborationCandidateService as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
  cs.listFriends.mockResolvedValue({
    ok: true,
    data: {
      items: [
        { id: 'b1', name: 'Alpha', online: true, status: 'online', reachability: 'reachable', visibility: 'public' },
      ],
      total: 1,
      offset: 0,
      limit: 50,
      hasMore: false,
    },
  });
  cs.listMine.mockResolvedValue({ ok: true, data: { items: [], total: 0, offset: 0, limit: 100, hasMore: false } });
  cs.listCandidates.mockResolvedValue({
    ok: true,
    data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false },
  });
});

it('adds selected collaborator bots from the friend tab', async () => {
  const onAddMany = jest.fn(async (ids: string[]) => ids.length);
  const view = render(
    <AddMemberDialog
      open
      existingIds={[]}
      activeIdentity={{ id: 'user-1', kind: 'user', displayName: '我', online: true }}
      onClose={jest.fn()}
      onAddMany={onAddMany}
    />,
  );

  expect(screen.getByRole('button', { name: '好友 Bot' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '已管理 Bot' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '可协作 Bot' })).toBeInTheDocument();

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认添加' }));

  await waitFor(() => expect(onAddMany).toHaveBeenCalledWith(['b1']));
  view.unmount();
});
