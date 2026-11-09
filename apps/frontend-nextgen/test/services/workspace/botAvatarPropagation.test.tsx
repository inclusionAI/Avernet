/** @jest-environment jsdom */
import { AvatarTile } from '@/pages/Workspace/components/AvatarTile';
import { IdentityAvatar } from '@/components/Workspace/IdentitySelector/IdentitySelectorParts';
import SquareBotCard from '@/components/CollaborationSquare/BotCard';
import { mapPublicBotCatalogDto } from '@/domain/collaborationSquare/mapper';
import { listBots, listBotMetadata } from '@/services/backendApi/bots/botController';
import { listCollaboratingBots } from '@/services/backendApi/bots/collaboratingBotController';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { botSessionService } from '@/services/workspace/botSessionService';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';

jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/backendApi/bots/collaboratingBotController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');

const avatar = '/assets/saved-bot.svg';

test.each([avatar, undefined, null, '', '  '])('三个对话目录保留自定义头像，缺失、空值或空白时与 Bot 管理一致（%p）', async (savedAvatar) => {
  const expected = savedAvatar?.trim() || generateBotAvatar('b1');
  const dto = { bot_id: 'b1', bot_name: 'Bot', avatar_url: savedAvatar, status: 'ACTIVE' };
  jest.mocked(listBots).mockResolvedValue({ code: 200000, data: {
    items: [{ ...dto, owner_entity_id: 'owner' }], total: 1,
  } });
  jest.mocked(listCollaboratingBots).mockResolvedValue({ code: 200000, data: {
    items: [{ ...dto, bot_desc: '', entity_id: 'owner', owner_id: 'owner', engine: 'openclaw',
      cluster_name: '', bot_type: 'personal', collaboration: { id: 1, role: 'admin', joined_at: '' } }], total: 1,
  } });
  jest.mocked(listFriendConnections).mockResolvedValue({ code: 20000, data: {
    items: [{ actor: { type: 'bot', id: 'b1:owner' } }], total: 1,
  } } as Awaited<ReturnType<typeof listFriendConnections>>);
  jest.mocked(listBotMetadata).mockResolvedValue({ code: 200000, data: {
    items: [{ ...dto, owner_id: 'owner' }], total: 1,
  } });
  expect(await botSessionService.listOwnedBotsWithMeta('149608')).toMatchObject({
    ok: true, data: { bots: [{ avatarUrl: expected }] },
  });
  expect(await teamBotConversationService.listBots('149608')).toMatchObject({
    ok: true, data: [{ avatarUrl: expected }],
  });
  expect(await collaborationCandidateService.listFriends('149608', { actorType: 'human' })).toMatchObject({
    ok: true, data: { items: [{ avatarUrl: expected }] },
  });
});

test.each([undefined, null, '', '  '])('发现和对话列表缺少头像时使用同一 Bot ID 的默认图片（%p）', (savedAvatar) => {
  const bot = mapPublicBotCatalogDto({ bot_id: 'b1', bot_uuid: 'b1:owner', name: '历史 Bot', avatar_url: savedAvatar });
  const expected = generateBotAvatar('b1');
  expect(bot?.avatarUrl).toBe(expected);
  render(<>
    <AvatarTile src={bot?.avatarUrl} label="对话 Bot" />
    <IdentityAvatar identity={{ id: 'b1:owner', name: '工作身份 Bot', kind: 'bot', avatar: expected }} />
    <SquareBotCard bot={bot!} activeActor={{ type: 'human', id: '149608' }} busy={false}
      onShare={jest.fn()} onPrimaryAction={jest.fn()} />
  </>);
  expect(screen.getByRole('img', { name: '历史 Bot' })).toHaveAttribute('src', expected);
  expect(screen.getByRole('img', { name: '对话 Bot' })).toHaveAttribute('src', expected);
  expect(screen.getByRole('img', { name: '工作身份 Bot' })).toHaveAttribute('src', expected);
});

test('发现卡片通过目录映射展示 Bot 自己的头像', () => {
  const bot = mapPublicBotCatalogDto({ bot_id: 'b1', name: 'Saved Bot', avatar_url: avatar });
  expect(bot?.avatarUrl).toBe(avatar);
  render(<SquareBotCard bot={bot!} activeActor={{ type: 'human', id: '149608' }} busy={false}
    onShare={jest.fn()} onPrimaryAction={jest.fn()} />);
  expect(screen.getByRole('img', { name: 'Saved Bot' })).toHaveAttribute('src', avatar);
});

test.each([avatar, 'data:image/svg+xml;base64,PHN2Zy8+'])('对话列表将 %s 作为图片渲染而非文字', (src) => {
  render(<AvatarTile src={src} label="Bot" />);
  expect(screen.getByRole('img', { name: 'Bot' })).toHaveAttribute('src', src);
});
