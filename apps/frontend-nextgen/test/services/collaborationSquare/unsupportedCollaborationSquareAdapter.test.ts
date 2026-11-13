import type { PublicBot } from '@/domain/collaborationSquare/types';
import type { CollaborationSquareGateway } from '@/services/collaborationSquare/collaborationSquareGateway';
import { UnsupportedCollaborationSquareAdapter } from '@/services/collaborationSquare/unsupportedCollaborationSquareAdapter';
import { describe, expect, it } from '@jest/globals';

const bot: PublicBot = {
  id: 'assistant:owner-8',
  name: 'Bot',
  ownerName: '未公开',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
};

describe('UnsupportedCollaborationSquareAdapter', () => {
  // 按网关契约访问，验证不支持能力显式抛 unsupported（不伪成功、不返回伪造结构）。
  const adapter: CollaborationSquareGateway = new UnsupportedCollaborationSquareAdapter();

  it('listBotPage / listBots 显式返回 unsupported，不伪造游标结构', async () => {
    await expect(adapter.listBotPage()).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.listBots()).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('discoverBots 显式返回 unsupported，不伪造 PublicBotDiscoveryResult', async () => {
    await expect(adapter.discoverBots({ keyword: '规划' })).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('getBotProfile（已选 PublicBot）显式返回 unsupported，不伪造摘要', async () => {
    await expect(adapter.getBotProfile(bot)).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('listGroupPage / listGroups / listGroupMembers / createGroupSession 显式返回 unsupported', async () => {
    await expect(adapter.listGroupPage()).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.listGroups()).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.listGroupMembers('g1')).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.createGroupSession('g1')).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('requestBotFriendship / openBotConversation 显式返回 unsupported', async () => {
    const humanContext = { actorId: 'human_900003', userId: '900003' };
    await expect(adapter.requestBotFriendship('b1', humanContext)).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.openBotConversation('b1', humanContext)).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('listPublicTasks / getPublicTask 显式返回 unsupported，不伪成功', async () => {
    await expect(adapter.listPublicTasks()).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.getPublicTask('task-1')).rejects.toMatchObject({ code: 'unsupported' });
  });
});
