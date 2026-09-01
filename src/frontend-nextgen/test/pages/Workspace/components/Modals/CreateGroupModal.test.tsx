/** @jest-environment jsdom */
import { CreateGroupModal } from '@/pages/Workspace/components/Modals/CreateGroupModal';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { groupService } from '@/services/workspace/groupService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// The production editor is lazy-loaded; this test exercises modal orchestration,
// not CodeMirror loading. A labelled textarea keeps the public Jest suite deterministic.
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea id="create-group-yaml" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

// bare auto-mock: factory referencing jest.fn() trips @jest/globals TDZ (repo convention).
jest.mock('@/services/workspace/groupService');

import { collaborationDefinitionService } from '@/services/workspace/collaborationDefinitionService';
jest.mock('@/services/workspace/collaborationCandidateService');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const gs = groupService as unknown as Record<string, jest.Mock<any>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cs = collaborationCandidateService as unknown as Record<string, jest.Mock<any>>;

const identity = { id: 'actor-1', kind: 'user' as const, displayName: '我', online: true };
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
  {
    id: 'b2',
    name: 'Beta',
    summary: '文案助手',
    online: true,
    status: 'online' as const,
    reachability: 'reachable' as const,
    visibility: 'public' as const,
    isFriend: true,
  },
];

function renderModal(overrides: Record<string, unknown> = {}) {
  return render(
    <CreateGroupModal open activeIdentity={identity} onClose={jest.fn()} onCreated={jest.fn()} {...overrides} />,
  );
}

async function selectLeader(label: string, optionName: RegExp) {
  fireEvent.click(screen.getByRole('button', { name: label }));
  fireEvent.click(await screen.findByRole('option', { name: optionName }));
}

beforeEach(() => {
  gs.createGroup.mockReset();
  cs.listFriends.mockReset();
  cs.listCandidates.mockReset();
  cs.listFriends.mockResolvedValue({
    ok: true,
    data: { items: bots, total: bots.length, offset: 0, limit: 50, hasMore: false },
  });
  cs.listCandidates.mockResolvedValue({
    ok: true,
    data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false },
  });
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue({
    ok: true,
    data: {
      valid: true,
      summary: { participants: 1, nodes: 1, initial_nodes: [] },
      participants: [{ binding: 'role-1', display_name: '助手', required: true, assigned: false }],
      errors: [],
    },
  });
});

it('free_chat strategy posts delivery_policy on confirm', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  const onCreated = jest.fn();
  renderModal({ onCreated });

  fireEvent.change(screen.getByLabelText('协作群名称'), { target: { value: '我的群' } });
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await selectLeader('群主 Bot', /Alpha/);
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'chat',
        name: '我的群',
        driverBotUuid: 'b1',
        participants: [{ actor_id: 'actor-1' }, { actor_id: 'b1' }],
      }),
    ),
  );
  expect(onCreated).toHaveBeenCalledWith('g9');
});

it('task_master_slave uses the selected manager as driver_bot_uuid', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  renderModal();

  fireEvent.click(screen.getByRole('radio', { name: '任务协作' }));
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await selectLeader('主节点（Manager Bot）', /Alpha/);
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'manager_worker',
        driverBotUuid: 'b1',
        participants: [{ actor_id: 'actor-1' }, { actor_id: 'b1' }],
      }),
    ),
  );
});

it('leader options only contain the currently selected bots', async () => {
  renderModal({ activeIdentity: botIdentity });
  await screen.findByRole('button', { name: /Alpha/ });

  fireEvent.click(screen.getByRole('button', { name: '群主 Bot' }));
  expect(await screen.findByRole('option', { name: /我 Bot/ })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /Alpha/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '群主 Bot' }));
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  fireEvent.click(screen.getByRole('button', { name: '群主 Bot' }));
  expect(await screen.findByRole('option', { name: /Alpha/ })).toBeInTheDocument();
});

it('state_machine submits definitionYaml as content_yaml body', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  renderModal({ activeIdentity: botIdentity });

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), {
    target: { value: 'participants:\n  - alpha\nroles:\n  - driver' },
  });
  // 校验 YAML 以通过 canSubmit
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  await waitFor(() => expect(collaborationDefinitionService.validate).toHaveBeenCalled());
  // 绑定 participant（绑定面板中的 Alpha 按钮）
  await new Promise<void>((resolve) => {
    setTimeout(() => resolve(), 100);
  });
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'state_machine',
        definitionYaml: 'participants:\n  - alpha\nroles:\n  - driver',
      }),
    ),
  );
});

it('backend 400 shows YAML error inline without closing', async () => {
  const onClose = jest.fn();
  gs.createGroup.mockResolvedValue({
    ok: false,
    error: { code: 'GROUP_CONFLICT', friendlyMessage: 'YAML 校验不通过: invalid role "driver"' },
  });
  renderModal({ activeIdentity: botIdentity, onClose });

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: 'a:\n  b' } });
  await new Promise<void>((resolve) => {
    setTimeout(() => resolve(), 50);
  });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  await waitFor(() => expect(collaborationDefinitionService.validate).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  expect(await screen.findByText(/YAML 校验不通过/)).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it('disables confirm until at least one participant is selected', async () => {
  renderModal();
  await screen.findByRole('button', { name: /Alpha/ });
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
});

it('user identity cannot choose custom collaboration', async () => {
  renderModal();
  expect(screen.getByRole('radio', { name: '自定义协作' })).toBeDisabled();
});

it('user identity shows the 已管理 Bot tab', async () => {
  renderModal();
  await screen.findByRole('button', { name: /Alpha/ });
  expect(screen.getByRole('button', { name: '已管理 Bot' })).toBeInTheDocument();
});

it('clears previous form state when reopened', async () => {
  const onClose = jest.fn();
  const onCreated = jest.fn();
  const view = render(<CreateGroupModal open activeIdentity={identity} onClose={onClose} onCreated={onCreated} />);
  await screen.findByRole('button', { name: /Alpha/ });

  fireEvent.change(screen.getByLabelText('协作群名称'), { target: { value: '旧配置' } });
  fireEvent.click(screen.getByRole('button', { name: /Alpha/ }));
  view.rerender(<CreateGroupModal open={false} activeIdentity={identity} onClose={onClose} onCreated={onCreated} />);
  view.rerender(<CreateGroupModal open activeIdentity={identity} onClose={onClose} onCreated={onCreated} />);

  await waitFor(() => expect((screen.getByLabelText('协作群名称') as HTMLInputElement).value).toBe(''));
  expect(screen.getByText('已选 0 个')).toBeInTheDocument();
});
