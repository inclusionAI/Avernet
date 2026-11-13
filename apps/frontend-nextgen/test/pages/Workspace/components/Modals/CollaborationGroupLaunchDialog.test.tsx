/** @jest-environment jsdom */
import '../../../../helpers/registerOpenGroupServiceBotRoutingSlots';
import CollaborationGroupLaunchDialog from '@/pages/Workspace/components/Modals/CollaborationGroupLaunchDialog';
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { collaborationDefinitionService } from '@/services/workspace/collaborationDefinitionService';
import { loadCollaborationDraft } from '@/services/workspace/collaborationDraftService';
import { navigateToLaunchedGroup } from '@/services/workspace/collaborationGroupLaunchNavigation';
import { launchCollaborationGroup } from '@/services/workspace/collaborationGroupLaunchService';
import { identityService } from '@/services/workspace/identityService';
import { useCollaborationGroupLaunchStore } from '@/stores/collaborationGroupLaunchStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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
// Keep the real validation interpretation; only its I/O is replaced.
jest.mock('@/services/workspace/collaborationDefinitionService', () => ({
  ...jest.requireActual('@/services/workspace/collaborationDefinitionService'),
  collaborationDefinitionService: { validate: jest.fn() },
}));

const draft = { schemaVersion: 2 as const, draftId: '12345678-1234-1234-1234-123456789abc', revision: 1 };
const bots = ['A', 'B'].map((name) => ({
  id: `bot-${name}:owner`,
  kind: 'bot' as const,
  displayName: `Bot ${name}`,
  online: true,
}));
const valid = {
  ok: true as const,
  data: {
    valid: true,
    summary: { participants: 1, nodes: 1, initial_nodes: [] },
    participants: [{ binding: 'worker', display_name: '执行者', required: true, assigned: false }],
    errors: [],
    warnings: [],
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  useCollaborationGroupLaunchStore.setState({ request: null, busy: false });
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities(bots, bots[0].id);
  jest
    .mocked(loadCollaborationDraft)
    .mockResolvedValue({ ok: true, data: { reference: draft, title: 'Draft 标题', definitionYaml: 'original YAML' } });
  jest
    .mocked(identityService.loadIdentities)
    .mockResolvedValue({ ok: true, data: { identities: bots, defaultActiveId: bots[0].id } });
  jest.mocked(collaborationDefinitionService.validate).mockResolvedValue(valid);
  jest.mocked(queryIsServiceBot).mockResolvedValue(false);
  const empty = { ok: true as const, data: { items: [], total: 0, offset: 0, limit: 50, hasMore: false } };
  jest.mocked(collaborationCandidateService.listFriends).mockResolvedValue(empty);
  jest.mocked(collaborationCandidateService.listCandidates).mockResolvedValue(empty);
  jest.mocked(launchCollaborationGroup).mockReset();
});

function openDialog() {
  useCollaborationGroupLaunchStore.getState().open('panel', { draft });
  return render(<CollaborationGroupLaunchDialog request={useCollaborationGroupLaunchStore.getState().request!} />);
}

async function chooseBot(name: string) {
  fireEvent.click(screen.getByRole('button', { name: '发起 Bot' }));
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(name) }));
}

async function bindOriginator(name: string) {
  const picker = await screen.findByTestId('group-participant-picker');
  fireEvent.click(await within(picker).findByRole('button', { name: new RegExp(name) }));
}

it('loads and validates the draft, fixes group kind, and retains edited YAML/goal while switching owned Bot', async () => {
  openDialog();
  await screen.findByRole('button', { name: '重新编辑' });
  expect(screen.getByLabelText('协作群名称')).toHaveValue('Draft 标题');
  expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('是否以任务执行')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('协作目标'), { target: { value: '我的目标' } });
  await bindOriginator('Bot A');
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '重新编辑' }));
  fireEvent.change(screen.getByLabelText('协作定义 YAML'), { target: { value: 'edited YAML' } });
  await chooseBot('Bot B');
  await waitFor(() => expect(collaborationDefinitionService.validate).toHaveBeenLastCalledWith('edited YAML'));
  expect(screen.getByLabelText('协作目标')).toHaveValue('我的目标');
  expect(screen.getByText('已绑定 0 / 1 个角色，共 0 个 Bot')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
  await waitFor(() =>
    expect(collaborationCandidateService.listCandidates).toHaveBeenCalledWith('bot-B:owner', expect.any(Object)),
  );
  await bindOriginator('Bot B');
  jest
    .mocked(launchCollaborationGroup)
    .mockResolvedValue({
      ok: true,
      data: { groupId: 'new-group', initialSessionId: 'initial', driverBotId: 'bot-B:owner' },
    });
  await waitFor(() => expect(screen.getByRole('button', { name: '确认创建' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await waitFor(() =>
    expect(navigateToLaunchedGroup).toHaveBeenCalledWith({
      groupId: 'new-group',
      initialSessionId: 'initial',
      driverBotId: 'bot-B:owner',
    }),
  );
  expect(launchCollaborationGroup).toHaveBeenCalledWith(
    expect.objectContaining({
      strategy: 'state_machine',
      originator: 'bot-B:owner',
      driverBotUuid: 'bot-B:owner',
      context: '我的目标',
      definitionYaml: 'edited YAML',
      participantBindings: [{ binding: 'worker', actor_ids: ['bot-B:owner'] }],
    }),
  );
});

it('prevents duplicate submits and keeps an unknown outcome locked with the edited form visible', async () => {
  let finish!: (value: Awaited<ReturnType<typeof launchCollaborationGroup>>) => void;
  jest.mocked(launchCollaborationGroup).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  openDialog();
  await screen.findByRole('button', { name: '重新编辑' });
  fireEvent.change(screen.getByLabelText('协作目标'), { target: { value: '目标' } });
  await bindOriginator('Bot A');
  const submit = screen.getByRole('button', { name: '确认创建' });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  fireEvent.click(submit);
  expect(launchCollaborationGroup).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: '发起 Bot' })).toBeDisabled();
  expect(useCollaborationGroupLaunchStore.getState().busy).toBe(true);
  await act(async () =>
    finish({
      ok: false,
      error: { code: 'GROUP_CREATE_OUTCOME_UNKNOWN', friendlyMessage: '结果待确认', canRetry: false },
    }),
  );
  expect(screen.getByRole('alert')).toHaveTextContent('结果待确认');
  expect(submit).toBeDisabled();
  expect(screen.getByLabelText('协作目标')).toHaveValue('目标');
  expect(navigateToLaunchedGroup).not.toHaveBeenCalled();
});

it('offers retry for failed draft reads and blocks launch without owned Bots', async () => {
  jest
    .mocked(loadCollaborationDraft)
    .mockResolvedValueOnce({
      ok: false,
      error: { code: 'DRAFT_READ_FAILED', friendlyMessage: '版本已撤销', canRetry: true },
    });
  jest
    .mocked(identityService.loadIdentities)
    .mockResolvedValue({ ok: true, data: { identities: [], defaultActiveId: '' } });
  openDialog();
  expect(await screen.findByRole('alert')).toHaveTextContent('版本已撤销');
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await screen.findByText('暂无可发起协作的自有 Bot，请先创建或绑定 Bot。');
  expect(screen.getByRole('button', { name: '确认创建' })).toBeDisabled();
});

it('offers retry when identities stall and ignores results from the timed-out attempt', async () => {
  jest.useFakeTimers();
  try {
    let finish!: (value: Awaited<ReturnType<typeof identityService.loadIdentities>>) => void;
    jest.mocked(identityService.loadIdentities).mockReturnValueOnce(new Promise((resolve) => {
      finish = resolve;
    }));
    openDialog();
    await act(async () => {
      await jest.advanceTimersByTimeAsync(14_999);
    });
    expect(screen.getByRole('status')).toHaveTextContent('正在读取协作版本和自有 Bot');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole('alert')).toHaveTextContent('超时');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await screen.findByRole('button', { name: '重新编辑' });
    expect(identityService.loadIdentities).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('协作群名称')).toHaveValue('Draft 标题');
    await act(async () => {
      finish({ ok: true, data: { identities: [], defaultActiveId: '' } });
    });
    expect(useWorkspaceStore.getState().identities).toEqual(bots);
    expect(screen.queryByText('暂无可发起协作的自有 Bot，请先创建或绑定 Bot。')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});
