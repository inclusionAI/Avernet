/** @jest-environment jsdom */
// collab 形态建群槽位回归（独立文件 = 独立模块注册表）：先注册全 null 槽位再静态 import
// CreateGroupModal——其模块作用域选路槽位（serviceBotRoutingSlots）按求值一次性绑定 noop
// 选路，execute 建群可用性在渲染期经 getExtensionSlots() 推导 null → 「是否以任务执行」
// 勾选框消失、自定义协作提交强制普通建群链路（不触任务引擎），定义 YAML 与
// participantBindings 原样保全。
// 不放回 CreateGroupModal.test.tsx：Open 形态既有用例依赖同一注册表在 import 前注册的
// 真实选路实现（模块求值一次性绑定，见 test/helpers/registerOpenGroupServiceBotRoutingSlots），
// 同文件内 resetModules+require 会落到另一份 react 实例（Invalid hook call），独立文件仅靠
// 声明序即可实现「null 注册先于模块求值零纠缠」（与 GroupChatComposerCollabSlots.test.tsx 同构）。
import '../../../../helpers/registerCollabNullSlots';
import { CreateGroupModal } from '@/pages/Workspace/components/Modals/CreateGroupModal';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { collaborationDefinitionService } from '@/services/workspace/collaborationDefinitionService';
import { groupService } from '@/services/workspace/groupService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.setTimeout(60000);

// 与 CreateGroupModal.test.tsx 同构的编辑器桩：本套驱动弹窗编排，不加载 CodeMirror。
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea id="create-group-yaml" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

// bare auto-mock: factory referencing jest.fn() trips @jest/globals TDZ (repo convention).
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/collaborationCandidateService');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const gs = groupService as unknown as Record<string, jest.Mock<any>>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cs = collaborationCandidateService as unknown as Record<string, jest.Mock<any>>;

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

// 与 Open 套件一致的校验固件：validate 通过并提供 role-1 绑定位。
const validYamlSummary = {
  ok: true,
  data: {
    valid: true,
    summary: { participants: 1, nodes: 1, initial_nodes: [] },
    participants: [{ binding: 'role-1', display_name: '助手', required: true, assigned: false }],
    errors: [],
    warnings: [],
  },
} as const;

beforeEach(() => {
  gs.createGroup.mockReset();
  gs.createGroupViaExecute.mockReset();
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
  cs.listMine.mockReset().mockResolvedValue({
    ok: true,
    data: { items: bots, total: bots.length, offset: 0, limit: 50, hasMore: false },
  });
  jest.spyOn(collaborationDefinitionService, 'validate').mockResolvedValue(validYamlSummary);
});

it('collab 空槽位：「是否以任务执行」勾选框消失，自定义协作按普通建群提交并保全 YAML/绑定', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g-collab' } });
  render(<CreateGroupModal open activeIdentity={botIdentity} onClose={jest.fn()} onCreated={jest.fn()} />);

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  // execute 建群可用性在渲染期按槽位推导：全 null 注册下勾选框整体消失。
  expect(screen.queryByRole('checkbox', { name: '是否以任务执行' })).not.toBeInTheDocument();

  const yaml = 'participants:\n  - alpha\nroles:\n  - driver';
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: yaml } });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  await waitFor(() => expect(collaborationDefinitionService.validate).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  // 提交强制普通建群链路：strategy state_machine + 原样 YAML / 绑定，且不触 execute 建群链路。
  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'state_machine',
        definitionYaml: yaml,
        driverBotUuid: 'b1',
        originator: 'actor-bot',
        participantBindings: [{ binding: 'role-1', actor_ids: ['b1'] }],
      }),
    ),
  );
  // bot 身份发起 task_dag:参与者即绑定面板勾选的 Bot(无人工号注入)。
  expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({ participants: [{ actor_id: 'b1' }] }));
  expect(gs.createGroupViaExecute).not.toHaveBeenCalled();
});

it('collab 空槽位下 @Human 通知来源对自定义协作仍可见并可提交（普通链路语义）', async () => {
  gs.createGroup.mockResolvedValue({ ok: true, data: { groupId: 'g-collab-notify' } });
  render(<CreateGroupModal open activeIdentity={botIdentity} onClose={jest.fn()} onCreated={jest.fn()} />);

  fireEvent.click(screen.getByRole('radio', { name: '自定义协作' }));
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), {
    target: { value: 'participants: [assistant]' },
  });
  fireEvent.click(screen.getByRole('button', { name: /校验 YAML/ }));
  await screen.findByRole('button', { name: '重新编辑' });
  // viaExecute 恒 false → humanNotifySlot 不被隐藏，随绑定面板展示。
  const notifyField = screen.getByRole('button', { name: '@Human 通知来源' });
  expect(notifyField).toHaveTextContent('所有成员');

  fireEvent.click(await screen.findByRole('button', { name: /Alpha/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));

  await waitFor(() =>
    expect(gs.createGroup).toHaveBeenCalledWith(expect.objectContaining({ humanMentionNotifyMode: 'all' })),
  );
  expect(gs.createGroupViaExecute).not.toHaveBeenCalled();
});
