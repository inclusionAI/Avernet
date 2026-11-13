/** @jest-environment jsdom */
// 注册必须先于弹窗 import：选路槽位在模块求值时固定，不能由 import 排序器移到文件尾。
import '../../../../helpers/registerCollabNullSlots';
import CollaborationGroupLaunchDialog from '@/pages/Workspace/components/Modals/CollaborationGroupLaunchDialog';
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { collaborationDefinitionService } from '@/services/workspace/collaborationDefinitionService';
import { loadCollaborationDraft } from '@/services/workspace/collaborationDraftService';
import { launchCollaborationGroup } from '@/services/workspace/collaborationGroupLaunchService';
import { identityService } from '@/services/workspace/identityService';
import { useCollaborationGroupLaunchStore } from '@/stores/collaborationGroupLaunchStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/workspace/botClassificationService');
jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/services/workspace/collaborationCandidateService');
jest.mock('@/services/workspace/collaborationDraftService');
jest.mock('@/services/workspace/collaborationGroupLaunchService');
jest.mock('@/services/workspace/collaborationGroupLaunchNavigation');
jest.mock('@/services/workspace/identityService');
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea id="create-group-yaml" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));
jest.mock('@/services/workspace/collaborationDefinitionService', () => ({
  ...jest.requireActual('@/services/workspace/collaborationDefinitionService'),
  collaborationDefinitionService: { validate: jest.fn() },
}));

it('collab 草稿建群保留 YAML/角色绑定，且不调用 TC 分类或展示任务执行入口', async () => {
  const draft = { schemaVersion: 2 as const, draftId: '12345678-1234-1234-1234-123456789abc', revision: 1 };
  const bot = { id: 'bot-external:owner', kind: 'bot' as const, displayName: '外部助手', online: true };
  useCollaborationGroupLaunchStore.setState({ request: null, busy: false });
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([bot], bot.id);
  jest.mocked(loadCollaborationDraft).mockResolvedValue({
    ok: true,
    data: {
      reference: draft,
      title: '协作草稿',
      definitionYaml: 'original YAML',
    },
  });
  jest.mocked(identityService.loadIdentities).mockResolvedValue({
    ok: true,
    data: {
      identities: [bot],
      defaultActiveId: bot.id,
    },
  });
  jest.mocked(collaborationDefinitionService.validate).mockResolvedValue({
    ok: true,
    data: {
      valid: true,
      summary: { participants: 1, nodes: 1, initial_nodes: [] },
      participants: [{ binding: 'worker', display_name: '执行者', required: true, assigned: false }],
      errors: [],
      warnings: [],
    },
  });
  const empty = { ok: true as const, data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false } };
  jest.mocked(collaborationCandidateService.listFriends).mockResolvedValue(empty);
  jest.mocked(collaborationCandidateService.listCandidates).mockResolvedValue(empty);
  jest
    .mocked(launchCollaborationGroup)
    .mockResolvedValue({ ok: true, data: { groupId: 'new-group', driverBotId: bot.id } });
  useCollaborationGroupLaunchStore.getState().open('panel', { draft });
  render(<CollaborationGroupLaunchDialog request={useCollaborationGroupLaunchStore.getState().request!} />);
  await screen.findByRole('button', { name: '重新编辑' });
  expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('是否以任务执行')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('协作目标'), { target: { value: '协作验证' } });
  const picker = await screen.findByTestId('group-participant-picker');
  fireEvent.click(await within(picker).findByRole('button', { name: /外部助手/ }));
  const submit = screen.getByRole('button', { name: '确认创建' });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await waitFor(() =>
    expect(launchCollaborationGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'state_machine',
        originator: bot.id,
        context: '协作验证',
        definitionYaml: 'original YAML',
        participants: [{ actor_id: bot.id }],
        participantBindings: [{ binding: 'worker', actor_ids: [bot.id] }],
      }),
    ),
  );
  expect(queryIsServiceBot).not.toHaveBeenCalled();
});
