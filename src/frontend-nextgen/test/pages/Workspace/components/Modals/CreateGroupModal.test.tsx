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

Object.defineProperty(globalThis, 'ResizeObserver', {
  configurable: true,
  value: class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
});

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
  cs.listMine.mockResolvedValue({
    ok: true,
    data: { items: bots, total: bots.length, offset: 0, limit: 50, hasMore: false },
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
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9', initialSessionId: 's-initial' } });
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
        originator: 'actor-1',
        participants: [{ actor_id: 'actor-1' }, { actor_id: 'b1' }],
      }),
    ),
  );
  expect(onCreated).toHaveBeenCalledWith({ groupId: 'g9', initialSessionId: 's-initial' });
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

it('falls back to participant names when the bot identity creates a group without a name', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  renderModal({ activeIdentity: botIdentity });

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Alpha、我 Bot',
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
        originator: 'actor-bot',
      }),
    ),
  );
});

it('shows the collaboration flow preview aside after YAML validation', async () => {
  const validate = collaborationDefinitionService.validate as jest.Mock;
  validate.mockResolvedValueOnce({
    ok: true,
    data: {
      valid: true,
      summary: { initial_nodes: ['node-1'] },
      participants: [{ binding: 'role-1', display_name: 'Alpha', required: true }],
      errors: [],
      graph: {
        graph_mode: 'acyclic',
        nodes: [
          {
            node_id: 'node-1',
            kind: 'bot_task',
            display_name: '处理任务',
            assignee: { type: 'bot_binding', binding: 'role-1' },
            final_output: true,
            judge: false,
          },
        ],
        edges: [],
      },
    },
  });
  renderModal({ activeIdentity: botIdentity });

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), {
    target: { value: 'participants:\n  - alpha\nroles:\n  - driver' },
  });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));

  await waitFor(() => {
    expect(screen.getByRole('complementary', { name: '协作流程侧栏', hidden: true })).toBeInTheDocument();
  });
  expect(screen.getByRole('region', { name: '协作流程预览' })).toBeInTheDocument();
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

it('keeps the managed Bot picker within the modal width', async () => {
  renderModal();
  await screen.findByRole('button', { name: /Alpha/ });

  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveClass('min-w-0');
  expect(screen.getByTestId('create-group-modal-body')).toHaveClass('min-w-0', 'max-w-full', 'overflow-x-hidden');
  expect(screen.getByTestId('group-participant-picker')).toHaveClass('w-full', 'min-w-0', 'max-w-full');
  const managedTab = screen.getByRole('button', { name: '已管理 Bot' });
  expect(managedTab.parentElement).toHaveClass('w-full', 'min-w-0');
  fireEvent.click(managedTab);
  expect(await screen.findByRole('button', { name: /Beta/ })).toBeInTheDocument();
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
