import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import * as controller from '@/services/backendApi/collaboration/sessionFileController';
import { resolveGroupGatewayOrigin } from '@/services/workspace/groupChatProviderHelpers';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionFileController');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/workspace/groupChatProviderHelpers');
const c = controller as unknown as Record<string, jest.Mock<any>>;
const bc = botController as unknown as Record<string, jest.Mock<any>>;
const gatewayOrigin = resolveGroupGatewayOrigin as jest.Mock<any>;

beforeEach(() => {
  jest.resetAllMocks();
  c.buildSessionFileContentUrl.mockReturnValue('/api/v1/collaboration/sessions/s1/files/f1/content?show=true');
});

const dto = {
  file_id: 'f1',
  session_id: 's1',
  file_name: 'a.md',
  mime_type: 'text/markdown',
  size: 1024,
  sha256: null,
  owner: { actor_kind: 'human' as const, actor_id: 'human_2088' },
  storage_backend: 'oss',
  status: 'ready' as const,
  created_at: 1700000000,
  updated_at: 1700000001,
};

it('loadFiles maps dto and resolves owner name from participants', async () => {
  c.listSessionFiles.mockResolvedValue({ data: { items: [dto], total: 1 } });
  const res = await sessionFileService.loadFiles('s1', [{ actorId: 'human_2088', name: '张三' } as never]);
  expect(res.ok).toBe(true);
  const item = res.ok ? res.data.items[0] : null;
  expect(item).toMatchObject({ fileId: 'f1', name: 'a.md', ownerName: '张三', status: 'ready', size: 1024 });
});

it('loadFiles falls back to cleaned actor_id when no participant matches', async () => {
  c.listSessionFiles.mockResolvedValue({ data: { items: [dto], total: 1 } });
  const res = await sessionFileService.loadFiles('s1', []);
  const item = res.ok ? res.data.items[0] : null;
  expect(item?.ownerName).toBe('2088');
});

it('resolveActorNames maps bot_id→name via bots/query, tolerating non-1:1 responses', async () => {
  bc.queryCollaborationBots.mockResolvedValue({
    data: {
      items: [
        { bot_id: 'human_2088', name: '张三' },
        { bot_id: 'bot:1', name: '小 Bot' },
      ],
    },
  });
  const map = await sessionFileService.resolveActorNames(['human_2088', 'bot:1', 'unknown_id']);
  expect(bc.queryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['human_2088', 'bot:1', 'unknown_id'] });
  expect(map).toEqual({ human_2088: '张三', 'bot:1': '小 Bot' });
  // 未返回的 id 不在映射中（调用方回退 actor_id）。
  expect(map.unknown_id).toBeUndefined();
});

it('resolveActorNames skips the request for empty input', async () => {
  const map = await sessionFileService.resolveActorNames([]);
  expect(map).toEqual({});
  expect(bc.queryCollaborationBots).not.toHaveBeenCalled();
});

it('resolveActorNames returns empty map on backend error (caller falls back to actor_id)', async () => {
  bc.queryCollaborationBots.mockRejectedValue(new Error('network'));
  const map = await sessionFileService.resolveActorNames(['human_2088']);
  expect(map).toEqual({});
});

it('buildContentUrl keeps same-origin relative path without deployed gateway', () => {
  gatewayOrigin.mockReturnValue(undefined);
  expect(sessionFileService.buildContentUrl('s1', 'f1')).toBe(
    '/api/v1/collaboration/sessions/s1/files/f1/content?show=true',
  );
});

it('buildContentUrl prefixes deployed gateway for render tags', () => {
  gatewayOrigin.mockReturnValue('https://gateway-pre.example.com');
  expect(sessionFileService.buildContentUrl('s1', 'f1')).toBe(
    'https://gateway-pre.example.com/api/v1/collaboration/sessions/s1/files/f1/content?show=true',
  );
});

it('removeFile returns ok with null on success', async () => {
  c.deleteSessionFile.mockResolvedValue({ data: {} });
  const res = await sessionFileService.removeFile('s1', 'f1');
  expect(res.ok).toBe(true);
});

it('shareFileWithExpiry maps backend unix seconds to frontend milliseconds', async () => {
  c.shareSessionFile.mockResolvedValue({
    data: { share_url: 'https://share.example/f1', share_token: 'tk', expires_at: 1700003600 },
  });
  const res = await sessionFileService.shareFileWithExpiry('s1', 'f1', 600);
  expect(c.shareSessionFile).toHaveBeenCalledWith('s1', 'f1', { ttl_seconds: 600 });
  expect(res).toEqual({
    ok: true,
    data: { shareUrl: 'https://share.example/f1', expiresAt: 1700003600000 },
  });
});

it('normalizes shared-file URLs from /openapi/v1 to /api/v1', async () => {
  c.shareSessionFile.mockResolvedValue({
    data: {
      share_url: '/openapi/v1/collaboration/sessions/shared-file/content?token=tk',
      share_token: 'tk',
      expires_at: 1700003600,
    },
  });

  const res = await sessionFileService.shareFileWithExpiry('s1', 'f1', 600);

  expect(res).toEqual({
    ok: true,
    data: {
      shareUrl: '/api/v1/collaboration/sessions/shared-file/content?token=tk',
      expiresAt: 1700003600000,
    },
  });
});

it('shareFile normalizes shared-file URLs from /openapi/v1 to /api/v1', async () => {
  c.shareSessionFile.mockResolvedValue({
    data: {
      share_url: '/openapi/v1/collaboration/sessions/shared-file/content?token=tk',
      share_token: 'tk',
      expires_at: 1700003600,
    },
  });

  const res = await sessionFileService.shareFile('s1', 'f1');

  expect(res).toEqual({
    ok: true,
    data: '/api/v1/collaboration/sessions/shared-file/content?token=tk',
  });
});
