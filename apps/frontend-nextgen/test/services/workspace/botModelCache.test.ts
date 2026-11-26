import { beforeEach, expect, it, jest } from '@jest/globals';
import { clearBotModelCache, loadCachedBotModels } from '@/services/workspace/botModelCache';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';

jest.mock('@/services/workspace/botSessionService');
const listModels = jest.mocked(botSessionService.listModels);
const bot: ChatBotView = { botId: 'bot:owner', realBotId: 'bot', ownerId: 'owner',
  displayName: 'Bot', botType: 'service', runtimeStage: 'online', online: true, chatable: true };
const result = { ok: true as const, data: [{ modelId: 'm', name: 'Model', provider: '' }] };

beforeEach(() => {
  clearBotModelCache();
  listModels.mockReset().mockResolvedValue(result);
});

it('合并在途请求，并在一分钟有效期内复用模型列表', async () => {
  const first = loadCachedBotModels(bot, 'viewer');
  expect(loadCachedBotModels({ ...bot }, 'viewer')).toBe(first);
  await first;
  await loadCachedBotModels(bot, 'viewer');
  expect(listModels).toHaveBeenCalledTimes(1);
});

it('不同 owner、stage、用户分别请求，切换用户清除旧缓存', async () => {
  await loadCachedBotModels(bot, 'viewer');
  await loadCachedBotModels({ ...bot, ownerId: 'another' }, 'viewer');
  await loadCachedBotModels({ ...bot, runtimeStage: 'draft' }, 'viewer');
  await loadCachedBotModels(bot, 'another-viewer');
  await loadCachedBotModels(bot, 'viewer');
  expect(listModels).toHaveBeenCalledTimes(5);
});

it('过期后重新读取，失败不缓存且允许重试', async () => {
  const now = jest.spyOn(Date, 'now').mockReturnValue(1000);
  try {
    await loadCachedBotModels(bot, 'viewer');
    now.mockReturnValue(61_001);
    listModels.mockResolvedValueOnce({ ok: false, error: {
      code: 'FAILED', friendlyMessage: '失败', canRetry: true,
    } });
    expect((await loadCachedBotModels(bot, 'viewer')).ok).toBe(false);
    expect((await loadCachedBotModels(bot, 'viewer')).ok).toBe(true);
    expect(listModels).toHaveBeenCalledTimes(3);
  } finally { now.mockRestore(); }
});

it('内存缓存最多保留 32 个上下文，淘汰后可重新加载', async () => {
  for (let i = 0; i < 33; i += 1) {
    await loadCachedBotModels({ ...bot, realBotId: `bot-${i}` }, 'viewer');
  }
  await loadCachedBotModels({ ...bot, realBotId: 'bot-0' }, 'viewer');
  expect(listModels).toHaveBeenCalledTimes(34);
});
