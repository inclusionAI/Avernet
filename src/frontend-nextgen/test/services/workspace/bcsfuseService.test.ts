import * as controller from '@/services/backendApi/bcsfuse/bcsfuseController';
import { bcsfuseService } from '@/services/workspace/bcsfuseService';
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bcsfuse/bcsfuseController');
const c = controller as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

const botParticipants = [
  { actorId: 'bot_a', kind: 'bot', name: 'Bot A', avatarUrl: 'https://example/a.png' },
  { actorId: 'worker_b', kind: 'bot', name: 'Bot B', avatarUrl: undefined },
  { actorId: 'human_1', kind: 'human', name: '张三', avatarUrl: undefined },
];

it('getFusionBots reads raw worker config instead of envelope data', async () => {
  c.getWorkerConfig.mockImplementation(async (workerId: string) => {
    if (workerId === 'bot_a') return { success: true, worker_id: workerId, fusion_enable: true, version: 1 };
    return { success: true, worker_id: workerId, fusion_enable: false, version: 1 };
  });

  const res = await bcsfuseService.getFusionBots(botParticipants as never);

  expect(c.getWorkerConfig).toHaveBeenCalledTimes(2);
  expect(res).toEqual({
    ok: true,
    data: [
      { botUuid: 'bot_a', name: 'Bot A', avatar: 'https://example/a.png', fusionEnable: true },
      { botUuid: 'worker_b', name: 'Bot B', avatar: undefined, fusionEnable: false },
    ],
  });
});

it('postFuse reads raw recommendation.summary', async () => {
  c.postFuse.mockResolvedValue({
    group_id: 'g1',
    recommendation: { summary: '融合后的回答' },
  });

  const res = await bcsfuseService.postFuse('g1', {
    session_id: 's1',
    question: '问题',
    driver_bot_id: 'bot_a',
    participants: ['bot_a'],
    fusion_mode: 'bot_profile_fuse',
    options: { timeout_ms: 180000 },
  });

  expect(res).toEqual({ ok: true, data: { summary: '融合后的回答', success: true } });
});

it('postFuse surfaces raw errors when recommendation is missing', async () => {
  c.postFuse.mockResolvedValue({ group_id: 'g1', errors: ['失败原因'] });

  const res = await bcsfuseService.postFuse('g1', {
    session_id: 's1',
    question: '问题',
    driver_bot_id: 'bot_a',
    participants: ['bot_a'],
    fusion_mode: 'bot_profile_fuse',
    options: { timeout_ms: 180000 },
  });

  expect(res).toEqual({ ok: true, data: { summary: '', success: false, error: '失败原因' } });
});
