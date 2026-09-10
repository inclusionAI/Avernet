/** @jest-environment jsdom */
import { CreateGroupModal } from '@/pages/Workspace/components/Modals/CreateGroupModal';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { groupService } from '@/services/workspace/groupService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea id="create-group-yaml" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/collaborationCandidateService');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const gs = groupService as unknown as Record<string, jest.Mock<any>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cs = collaborationCandidateService as unknown as Record<string, jest.Mock<any>>;

const userIdentity = { id: 'actor-1', kind: 'user' as const, displayName: '我', online: true };
const botIdentity = { id: 'actor-bot', kind: 'bot' as const, displayName: '我 Bot', online: true };
const bots = [
  {
    id: 'b1',
    name: 'Alpha',
    summary: '代码助手',
    online: true,
    status: 'online' as const,
    reachability: 'reachable' as const,
    visibility: 'public' as const,
    isFriend: true,
  },
];

Object.defineProperty(globalThis, 'ResizeObserver', {
  configurable: true,
  value: class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
});

beforeEach(() => {
  gs.createGroup.mockReset();
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  cs.listFriends.mockReset();
  cs.listCandidates.mockReset();
  cs.listMine.mockReset();
  cs.listFriends.mockResolvedValue({ ok: true, data: { items: bots, total: 1, offset: 0, limit: 50, hasMore: false } });
  cs.listCandidates.mockResolvedValue({
    ok: true,
    data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false },
  });
  cs.listMine.mockResolvedValue({ ok: true, data: { items: bots, total: 1, offset: 0, limit: 50, hasMore: false } });
});

async function selectLeader(label: string, optionName: RegExp) {
  fireEvent.click(screen.getByRole('button', { name: label }));
  fireEvent.click(await screen.findByRole('option', { name: optionName }));
}

it('用户身份建群展示视角 radio，选参与者视角后 human 参与者条目携带 scope', async () => {
  render(<CreateGroupModal open activeIdentity={userIdentity} onClose={jest.fn()} onCreated={jest.fn()} />);
  const radios = screen.getAllByRole('radio', { name: /完整视角|参与者视角/ });
  expect(radios).toHaveLength(2);
  expect(radios[0]).toBeChecked();
  fireEvent.click(radios[1]);

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await selectLeader('群主 Bot', /Alpha/);
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        participants: [{ actor_id: 'actor-1', message_view_scope: 'participant' }, { actor_id: 'b1' }],
      }),
    ),
  );
}, 30_000);

it('bot 身份建群不展示视角 radio 且参与者不携带 scope', async () => {
  render(<CreateGroupModal open activeIdentity={botIdentity} onClose={jest.fn()} onCreated={jest.fn()} />);
  expect(screen.queryAllByRole('radio', { name: /完整视角|参与者视角/ })).toHaveLength(0);
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        participants: [{ actor_id: 'actor-bot' }, { actor_id: 'b1' }],
      }),
    ),
  );
});
