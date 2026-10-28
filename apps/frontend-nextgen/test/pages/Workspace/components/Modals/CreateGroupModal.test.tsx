/** @jest-environment jsdom */
import { CreateGroupModal } from '@/pages/Workspace/components/Modals/CreateGroupModal';
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { groupService } from '@/services/workspace/groupService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// 建群表单重度使用 Radix Select/Popover 等组件，jsdom 下异步渲染开销大：
// 并行全量（--maxWorkers=50%）下个别用例在默认 30s 随机临界超时。
// 统一放宽至 60s（仅本文件生效，不改全局 testTimeout，不改任何断言）。
jest.setTimeout(60000);

// The production editor is lazy-loaded; this test exercises modal orchestration,
// not CodeMirror loading. A labelled textarea keeps the public Jest suite deterministic.
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea id="create-group-yaml" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

// bare auto-mock: factory referencing jest.fn() trips @jest/globals TDZ (repo convention).
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/botClassificationService');

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
  jest.mocked(queryIsServiceBot).mockReset().mockResolvedValue(false);
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
      warnings: [],
    },
  });
});

it('发起协作顶栏不再展示身份 chip，仅保留标题', () => {
  render(
    <CreateGroupModal
      open
      activeIdentity={{ id: 'human_900004', kind: 'user', displayName: '900004', online: true }}
      authenticatedUserId="900004"
      authenticatedUserName="示例用户"
      onClose={jest.fn()}
      onCreated={jest.fn()}
    />,
  );
  expect(screen.getByRole('heading', { name: '发起协作' })).toBeInTheDocument();
  // 「为 xxx」身份 chip 已移除：顶栏不再展示认证用户名 / 身份类型徽标。
  expect(screen.queryByText('示例用户')).not.toBeInTheDocument();
  expect(screen.queryByText('为')).not.toBeInTheDocument();
});

it('Bot 身份未命名时自动群名保留自身名称而非认证用户名', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  render(
    <CreateGroupModal
      open
      activeIdentity={{ id: 'bot_xxx:900004', kind: 'bot', displayName: '协作 Bot', online: true }}
      authenticatedUserId="900004"
      authenticatedUserName="示例用户"
      onClose={jest.fn()}
      onCreated={jest.fn()}
    />,
  );
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({ name: expect.stringContaining('协作 Bot') })),
  );
  expect(gs.createGroup).not.toHaveBeenCalledWith(
    expect.objectContaining({ name: expect.stringContaining('示例用户') }),
  );
});

it('free_chat strategy posts delivery_policy on confirm', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9', initialSessionId: 's-initial' } });
  const onCreated = jest.fn();
  renderModal({ onCreated });

  fireEvent.change(screen.getByLabelText('协作群名称'), { target: { value: '我的群' } });
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await selectLeader('群主 Bot', /Alpha/);
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'chat',
        humanMentionNotifyMode: 'all',
        name: '我的群',
        driverBotUuid: 'b1',
        originator: 'actor-1',
        participants: [{ actor_id: 'actor-1', message_view_scope: 'full' }, { actor_id: 'b1' }],
      }),
    ),
  );
  expect(onCreated).toHaveBeenCalledWith({ groupId: 'g9', initialSessionId: 's-initial' });
});

it('maps the auto-reply switch to the delivery policy and updates its helper text', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  renderModal();

  expect(screen.getByTestId('create-group-modal-body')).toHaveClass('gap-3');
  expect(screen.getByText('协作群名称')).toHaveClass('text-foreground');
  expect(screen.getByText('协作目标')).toHaveClass('text-foreground');
  expect(screen.getByTestId('free-chat-settings-grid')).toHaveClass('gap-3');

  const autoReply = screen.getByRole('switch', { name: '自动回复' });
  const notifyField = screen.getByRole('button', { name: '@Human 通知来源' });
  expect(screen.getByTestId('free-chat-settings-grid')).toContainElement(notifyField);
  expect(autoReply.compareDocumentPosition(notifyField)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(autoReply).toBeChecked();
  expect(screen.getByText('群主 Bot 将默认回复每一条消息')).toHaveClass('text-muted-foreground');
  expect(screen.getByTestId('free-chat-settings-grid')).toHaveClass(
    'sm:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)_minmax(0,1fr)]',
  );

  fireEvent.click(autoReply);
  expect(autoReply).not.toBeChecked();
  expect(screen.getByText('群主 Bot 仅在被 @ 时或上下文语境高度关联时答复')).toBeInTheDocument();

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await selectLeader('群主 Bot', /Alpha/);
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({ deliveryPolicy: 'inject_observers' })),
  );
});

it.each([
  ['自由聊天', 'chat', '不通知', 'none'],
  ['任务协作', 'manager_worker', '仅群主 Bot', 'driver_bot_only'],
  ['自定义协作', 'state_machine', '不通知', 'none'],
] as const)('%s 提交所选 @Human 通知来源', async (label, strategy, option, mode) => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g-notify' } });
  renderModal({ activeIdentity: botIdentity });
  fireEvent.click(screen.getByRole('radio', { name: label }));
  if (strategy === 'state_machine') {
    fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: 'participants: [assistant]' } });
    fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
    await screen.findByRole('button', { name: '重新编辑' });
    fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  } else if (strategy === 'manager_worker') {
    await selectLeader('主节点（Manager Bot）', /我 Bot/);
  }
  const field = screen.getByRole('button', { name: '@Human 通知来源' });
  expect(field).toHaveTextContent('所有成员');
  await selectLeader('@Human 通知来源', new RegExp(`^${option}$`));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({ strategy, humanMentionNotifyMode: mode })),
  );
});

it('重新打开建群弹窗将通知来源重置为所有成员', async () => {
  const props = { activeIdentity: botIdentity, onClose: jest.fn(), onCreated: jest.fn() };
  const view = render(<CreateGroupModal open {...props} />);
  await selectLeader('@Human 通知来源', /^不通知$/);
  fireEvent.click(screen.getByRole('radio', { name: '任务协作' }));
  expect(screen.getByRole('button', { name: '@Human 通知来源' })).toHaveTextContent('不通知');
  view.rerender(<CreateGroupModal open={false} {...props} />);
  view.rerender(<CreateGroupModal open {...props} />);
  expect(screen.getByRole('button', { name: '@Human 通知来源' })).toHaveTextContent('所有成员');
});

it('任务执行建群隐藏通知来源且不提交该字段', async () => {
  gs.createGroupViaExecute.mockReset().mockResolvedValue({ ok: true, data: { groupId: 'g-execute' } });
  renderModal({ activeIdentity: botIdentity });
  await selectLeader('@Human 通知来源', /^不通知$/);
  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  expect(screen.queryByRole('button', { name: '@Human 通知来源' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: 'participants: [assistant]' } });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  await screen.findByRole('button', { name: '重新编辑' });
  expect(screen.getByRole('button', { name: '@Human 通知来源' })).toHaveTextContent('不通知');
  fireEvent.click(screen.getByRole('checkbox', { name: '是否以任务执行' }));
  expect(screen.queryByRole('button', { name: '@Human 通知来源' })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() => expect(gs.createGroupViaExecute).toHaveBeenCalled());
  expect(gs.createGroupViaExecute.mock.calls[0][0]).not.toHaveProperty('humanMentionNotifyMode');
  expect(gs.createGroup).not.toHaveBeenCalled();
});

it('task_master_slave uses the selected manager as driver_bot_uuid', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  renderModal();

  fireEvent.click(screen.getByRole('radio', { name: '任务协作' }));
  expect(screen.queryByText('主节点统一推进任务，其余成员作为 Worker 配合执行。')).not.toBeInTheDocument();
  const manager = screen.getByRole('button', { name: '主节点（Manager Bot）' });
  const notifyField = screen.getByRole('button', { name: '@Human 通知来源' });
  expect(manager.parentElement?.parentElement?.parentElement).toContainElement(notifyField);
  expect(manager.compareDocumentPosition(notifyField)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await selectLeader('主节点（Manager Bot）', /Alpha/);
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'manager_worker',
        driverBotUuid: 'b1',
        participants: [{ actor_id: 'actor-1', message_view_scope: 'full' }, { actor_id: 'b1' }],
      }),
    ),
  );
});

it('falls back to participant names when the bot identity creates a group without a name', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g9' } });
  renderModal({ activeIdentity: botIdentity });

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
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
  expect(screen.getByText('是否以任务执行')).toHaveClass('text-xs', 'font-semibold');
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
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
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
      warnings: [],
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
  expect(screen.getByRole('dialog')).toHaveClass('max-w-2xl', 'xl:left-[calc(50%-208px)]', 'xl:overflow-visible');
  expect(screen.getByRole('complementary', { name: '协作流程侧栏', hidden: true })).toHaveClass(
    'absolute',
    'left-[calc(100%+1rem)]',
    'flex-col',
    'w-[400px]',
    'xl:flex',
  );
  expect(screen.getByTestId('collaboration-flow-content')).toHaveClass('flex-1', 'items-center');
  expect(screen.getByTestId('create-group-modal-body')).toHaveClass('lg:overflow-y-auto');
  expect(screen.getByTestId('group-participant-list')).toHaveClass('max-h-[420px]', 'pb-2');
  expect(screen.getByText('协同剧本')).toHaveClass('text-xs', 'font-semibold');
  const notifyField = screen.getByRole('button', { name: '@Human 通知来源' });
  const scriptFrame = screen.getByText('协同剧本').parentElement?.parentElement;
  expect(scriptFrame).toHaveClass('border');
  expect(scriptFrame).not.toContainElement(notifyField);
  expect(scriptFrame).toContainElement(screen.getByRole('button', { name: '重新编辑' }));
  expect(scriptFrame?.parentElement).toHaveClass('grid', 'grid-cols-1', 'sm:grid-cols-2');
  expect(scriptFrame?.nextElementSibling).toContainElement(notifyField);
  expect(screen.getByRole('button', { name: '重新编辑' })).toHaveClass('text-xs');
});

it('previews a validation-only Loop but blocks creation until the server permits execution', async () => {
  const response = JSON.parse(readFileSync(resolve(__dirname, '../../../../../../bcs/tests/fixtures/fixed_loop_preview.json'), 'utf8'));
  const validate = jest.mocked(collaborationDefinitionService.validate);
  validate.mockResolvedValueOnce({ ok: true, data: response });
  renderModal({ activeIdentity: botIdentity });
  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  const yaml = readFileSync(resolve(__dirname, '../../../../../../bcs/tests/fixtures/fixed_loop_authoring.yaml'), 'utf8');
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: yaml } });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  expect(await screen.findByText(/暂不能创建协作群/)).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('VALIDATION_ONLY_FEATURE');
  expect(screen.getByRole('status')).toHaveTextContent('$.runtime.state_machine.version');
  expect(screen.getByRole('region', { name: '协作流程预览' })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  expect(gs.createGroup).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: '重新编辑' }));
  validate.mockResolvedValueOnce({ ok: true, data: { ...response, warnings: [] } });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  await waitFor(() => expect(screen.queryByText(/暂不能创建协作群/)).not.toBeInTheDocument());
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'loop-group' } });
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() => expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({
    definitionYaml: yaml, strategy: 'state_machine', participantBindings: [{ binding: 'writer', actor_ids: ['b1'] }],
  })));
});

it('shows YAML validation errors on the left side of the footer action row', async () => {
  const validate = collaborationDefinitionService.validate as jest.Mock;
  validate.mockResolvedValueOnce({
    ok: true,
    data: {
      valid: false,
      summary: { participants: 0, nodes: 0, initial_nodes: [] },
      participants: [],
      errors: [{ code: 'INVALID_YAML', path: '$', message: '角色定义无效' }],
      warnings: [],
    },
  });
  renderModal({ activeIdentity: botIdentity });

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: 'participants: invalid' } });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));

  const error = await screen.findByText(/角色定义无效/);
  const footer = screen.getByTestId('create-group-modal-footer');
  expect(footer).toContainElement(error);
  expect(screen.getByTestId('create-group-modal-body')).not.toContainElement(error);
  expect(footer).toContainElement(screen.getByRole('button', { name: '取消' }));
  expect(footer).toContainElement(screen.getByRole('button', { name: '确认创建' }));
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
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  expect(await screen.findByText(/YAML 校验不通过/)).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it('disables confirm until at least one participant is selected', async () => {
  renderModal();
  await screen.findByRole('button', { name: /Alpha/ });
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
});

it('协作目标智能推荐落地后自动全选推荐结果', async () => {
  cs.listCandidates.mockResolvedValue({
    ok: true,
    data: { items: bots, total: bots.length, offset: 0, limit: 50, hasMore: false },
  });
  cs.recommendCandidates.mockResolvedValue({
    ok: true,
    data: [
      {
        id: 'b1',
        name: 'Alpha',
        online: true,
        status: 'online',
        reachability: 'reachable',
        recommendScore: 0.9,
        shortProfile: '值班相关',
      },
    ],
  });
  renderModal();

  fireEvent.change(screen.getByLabelText('协作目标'), { target: { value: '提升值班效率' } });
  fireEvent.click(screen.getByRole('button', { name: '智能推荐可协作 Bot' }));

  // 推荐链路含 350ms 防抖，且本组件在 jsdom 下渲染开销大，放宽 waitFor 超时。
  await waitFor(
    () => {
      expect(screen.getByText('已选 1 个')).toBeInTheDocument();
    },
    { timeout: 5000 },
  );
  expect(screen.getByTestId('group-participant-selected')).toHaveTextContent('Alpha');
});

it('再次智能推荐不勾选用户取消过的 Bot，新出现的结果仍自动选中', async () => {
  cs.listCandidates.mockResolvedValue({
    ok: true,
    data: { items: bots, total: bots.length, offset: 0, limit: 50, hasMore: false },
  });
  cs.recommendCandidates.mockResolvedValue({
    ok: true,
    data: [
      { id: 'b1', name: 'Alpha', online: true, status: 'online', reachability: 'reachable', recommendScore: 0.9 },
      { id: 'b2', name: 'Beta', online: true, status: 'online', reachability: 'reachable', recommendScore: 0.8 },
    ],
  });
  renderModal();

  fireEvent.change(screen.getByLabelText('协作目标'), { target: { value: '值班' } });
  fireEvent.click(screen.getByRole('button', { name: '智能推荐可协作 Bot' }));
  await waitFor(
    () => {
      expect(screen.getByText('已选 2 个')).toBeInTheDocument();
    },
    { timeout: 5000 },
  );

  // 用户取消 Alpha：再次推荐时取消优先，不应被自动勾回。
  fireEvent.click(within(screen.getByTestId('group-participant-list')).getByRole('button', { name: /Alpha/ }));
  await waitFor(
    () => {
      expect(screen.getByText('已选 1 个')).toBeInTheDocument();
    },
    { timeout: 5000 },
  );
  expect(screen.getByTestId('group-participant-selected')).not.toHaveTextContent('Alpha');

  // 修改目标再次智能推荐：Alpha 保持取消，Beta 保持选中。
  fireEvent.change(screen.getByLabelText('协作目标'), { target: { value: '值班响应' } });
  fireEvent.click(screen.getByRole('button', { name: '智能推荐可协作 Bot' }));
  await waitFor(
    () => {
      expect(
        within(screen.getByTestId('group-participant-list')).getByRole('button', { name: /Alpha/ }),
      ).toBeInTheDocument();
    },
    { timeout: 5000 },
  );
  expect(screen.getByText('已选 1 个')).toBeInTheDocument();
  expect(screen.getByTestId('group-participant-selected')).toHaveTextContent('Beta');
  expect(screen.getByTestId('group-participant-selected')).not.toHaveTextContent('Alpha');
});

it('按画像筛选搜索结果不自动选中', async () => {
  cs.listCandidates.mockResolvedValue({
    ok: true,
    data: { items: bots, total: bots.length, offset: 0, limit: 50, hasMore: false },
  });
  cs.recommendCandidates.mockResolvedValue({
    ok: true,
    data: [
      {
        id: 'b1',
        name: 'Alpha',
        online: true,
        status: 'online',
        reachability: 'reachable',
        recommendScore: 0.9,
        shortProfile: '值班相关',
      },
    ],
  });
  renderModal();

  fireEvent.click(screen.getByRole('button', { name: '可协作 Bot' }));
  fireEvent.click(screen.getByText('按画像筛选'));
  const profileSearchInput = await screen.findByLabelText('按画像关键词搜索可协作 Bot');
  fireEvent.change(profileSearchInput, { target: { value: '值班' } });

  await waitFor(
    () => {
      expect(
        within(screen.getByTestId('group-participant-list')).getByRole('button', { name: /Alpha/ }),
      ).toBeInTheDocument();
    },
    { timeout: 5000 },
  );
  expect(screen.getByText('已选 0 个')).toBeInTheDocument();
});

it('keeps the create actions outside the scroll region after selecting a bot', async () => {
  renderModal();

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));

  const scrollRegion = screen.getByTestId('create-group-modal-body');
  const footer = screen.getByTestId('create-group-modal-footer');
  expect(scrollRegion).toHaveClass('min-h-0', 'overflow-y-auto');
  expect(scrollRegion).not.toContainElement(footer);
  expect(footer).toContainElement(screen.getByRole('button', { name: '确认创建' }));
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

it('uses a taller viewport-aware shell without the previous 560px body cap', () => {
  renderModal();

  expect(screen.getByRole('dialog')).toHaveClass(
    'max-w-2xl',
    'h-[min(860px,calc(100vh-2rem))]',
    'grid-rows-[auto_minmax(0,1fr)_auto]',
  );
  expect(screen.getByTestId('create-group-modal-body')).toHaveClass(
    'h-full',
    'min-h-0',
    'overflow-y-auto',
    'lg:overflow-y-hidden',
  );
  expect(screen.getByTestId('create-group-modal-body')).not.toHaveClass('max-h-[560px]');
});

it('does not show the passive waiting-for-valid-YAML hint', () => {
  renderModal({ activeIdentity: botIdentity });

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  expect(screen.queryByText('等待输入有效 YAML')).not.toBeInTheDocument();
  expect(screen.getByTestId('custom-yaml-section')).toHaveClass('flex', 'min-h-0', 'flex-1', 'flex-col');
});

it('keeps the managed Bot picker within the modal width', async () => {
  renderModal();
  await screen.findByRole('button', { name: /Alpha/ });

  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveClass('min-w-0');
  expect(screen.getByTestId('create-group-modal-body')).toHaveClass(
    'min-w-0',
    'max-w-full',
    'flex',
    'flex-col',
    'overflow-x-hidden',
  );
  expect(screen.getByTestId('group-participant-picker')).toHaveClass(
    'w-full',
    'min-h-0',
    'min-w-0',
    'max-w-full',
    'flex-1',
  );
  expect(screen.getByTestId('group-participant-list')).toHaveClass('h-full', 'max-h-none');
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

it.each(['自由聊天', '任务协作', '自定义协作'])('%s carries the selected service Bot stage tag', async (kind) => {
  jest.mocked(queryIsServiceBot).mockImplementation(async (id: string) => id === 'b1');
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g-stage' } });
  renderModal({ activeIdentity: botIdentity });
  if (kind !== '自由聊天') fireEvent.click(screen.getByRole('radio', { name: kind }));
  if (kind === '自定义协作') {
    fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: 'participants: [alpha]' } });
    fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
    await screen.findByRole('button', { name: /Alpha/ });
  }
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  if (kind === '任务协作') await selectLeader('主节点（Manager Bot）', /Alpha/);
  fireEvent.click(await screen.findByRole('button', { name: '指定路由' }));
  const selectedMembers = screen.getByTestId('group-participant-selected');
  const serviceBotChip = within(selectedMembers).getByRole('group', { name: 'Alpha已选成员' });
  expect(within(serviceBotChip).getByRole('group', { name: 'Alpha路由方式' })).toBeInTheDocument();
  expect(within(serviceBotChip).getByRole('group', { name: 'Alpha运行阶段' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '服务 Bot 路由' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '预发' }));
  expect(within(serviceBotChip).getByText('Alpha（预发）')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        participants: expect.arrayContaining([{ actor_id: 'b1', tags: ['verify'] }]),
      }),
    ),
  );
});

it('shows classification failures, blocks submit, and retries before allowing dynamic routing', async () => {
  jest.mocked(queryIsServiceBot).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(true);
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g-dynamic' } });
  renderModal({ activeIdentity: botIdentity });
  await screen.findByText('Bot 类型识别失败，请重试');
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await screen.findByRole('button', { name: '动态路由规则' });
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '指定路由' }));
  fireEvent.click(screen.getByRole('button', { name: '草稿' }));
  fireEvent.click(screen.getByRole('button', { name: '动态路由规则' }));
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        participants: [{ actor_id: 'actor-bot' }],
      }),
    ),
  );
});
