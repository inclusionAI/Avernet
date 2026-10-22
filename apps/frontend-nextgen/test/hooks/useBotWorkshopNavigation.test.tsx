/** @jest-environment jsdom */
import { useBotWorkshopNavigation } from '@/hooks/useBotWorkshopNavigation';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { buildAgentCodingChatPath, workspaceService } from '@/services/workspace';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/services/workspace', () => ({
  buildAgentCodingChatPath: jest.fn(),
  workspaceService: { switchIdentity: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { error: jest.fn() } }));

const mockedBuildAgentCodingChatPath = buildAgentCodingChatPath as jest.MockedFunction<typeof buildAgentCodingChatPath>;

const baseBot = {
  id: 'bot-1',
  ownerId: 'owner-1',
  entityKey: 'space:bot:bot-1',
  name: 'Bot',
  spaceKind: 'personal' as const,
  ownership: 'personal' as const,
  deployment: 'cloud' as const,
  serviceMode: 'non-service' as const,
  canUpgradeToService: false,
  lifecycle: 'running' as const,
  runtime: { isAgentCodingBot: false },
  completeness: 'complete' as const,
  warnings: [],
  actions: [],
  disabledActions: {},
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedBuildAgentCodingChatPath.mockReturnValue('/coding/coding-chat?botId=bot-1');
  useWorkspaceStore
    .getState()
    .setIdentities([{ id: 'current-user', kind: 'user', displayName: '当前用户', online: true }], 'current-user');
});

it('个人 Bot 仍以 managed 路由和 owner 上下文打开会话', () => {
  const { result } = renderHook(() => useBotWorkshopNavigation());

  act(() => result.current.openConversation(baseBot as never));

  expect(workspaceService.switchIdentity).toHaveBeenCalledWith('current-user');
  expect(history.push).toHaveBeenCalledWith('/workspace/chat?section=managed&bot=bot-1%3Aowner-1&origin=mine');
});

it('团队 Bot 使用 team 路由和所属 entity 上下文打开会话', () => {
  const { result } = renderHook(() => useBotWorkshopNavigation());
  const teamBot = {
    ...baseBot,
    spaceKind: 'team' as const,
    harnessContext: { entityType: 'team', entityId: 'team-entity-1' },
  };

  act(() => result.current.openConversation(teamBot as never));

  expect(history.push).toHaveBeenCalledWith('/workspace/chat?section=team&bot=bot-1%3Ateam-entity-1&origin=mine');
});

it('真实 inventory 响应经 mapper 后以 entity_id 路由，并保留 owner_entity_id 作为 owner', () => {
  const { result } = renderHook(() => useBotWorkshopNavigation());
  const inventoryDto = {
    bot_id: 'team-bot-1',
    bot_name: '团队协作者 Bot',
    bot_desc: 'fixture',
    engine: 'openclaw',
    bot_type: 'personal',
    kind: 'personal_cloud',
    deploy_mode: 'cloud',
    display_state: 'running',
    status: 'ACTIVE',
    entity_id: 'team_spc-example',
    owner_entity_id: 'owner-user-1',
    space: { space_id: '252', name: 'Fixture Team', kind: 'team' },
    actions: ['view', 'chat', 'edit'],
  };
  const bot = mapBotDto(inventoryDto).item;

  act(() => result.current.openConversation(bot));

  expect(bot.ownerId).toBe('owner-user-1');
  expect(history.push).toHaveBeenCalledWith(
    '/workspace/chat?section=team&bot=team-bot-1%3Ateam_spc-example&origin=mine',
  );
});

it('团队空间内的 Owner Bot 保持 managed owner 路由', () => {
  const { result } = renderHook(() => useBotWorkshopNavigation());
  useWorkspaceStore
    .getState()
    .setIdentities([{ id: 'human_900003', kind: 'user', displayName: '当前用户', online: true }], 'human_900003');
  const ownerBot = mapBotDto({
    bot_id: 'team-owner-bot-1',
    bot_name: '团队空间 Owner Bot',
    bot_desc: 'owner fixture',
    engine: 'openclaw',
    bot_type: 'personal',
    kind: 'personal_cloud',
    deploy_mode: 'cloud',
    display_state: 'running',
    status: 'ACTIVE',
    entity_id: 'team_spc-example',
    owner_entity_id: '900003',
    space: { space_id: '252', name: 'Fixture Team', kind: 'team' },
  }).item;

  act(() => result.current.openConversation(ownerBot));

  expect(history.push).toHaveBeenCalledWith(
    '/workspace/chat?section=managed&bot=team-owner-bot-1%3A900003&origin=mine',
  );
});

it('AgentCoding Bot 继续使用专用对话路由', () => {
  const { result } = renderHook(() => useBotWorkshopNavigation());
  const agentCodingTeamBot = {
    ...baseBot,
    spaceKind: 'team' as const,
    spaceId: 'team-space-1',
    spaceName: '团队空间',
    runtime: { isAgentCodingBot: true },
  };

  act(() => result.current.openConversation(agentCodingTeamBot as never));

  expect(mockedBuildAgentCodingChatPath).toHaveBeenCalledWith({
    botId: 'bot-1',
    spaceId: 'team-space-1',
    spaceName: '团队空间',
  });
  expect(history.push).toHaveBeenCalledWith('/coding/coding-chat?botId=bot-1');
});
