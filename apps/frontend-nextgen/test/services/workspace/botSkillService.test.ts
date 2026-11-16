import {
  botSkillService,
  TASK_INTENT_INSTALLED_BOTS_CONFIG,
} from '@/services/workspace/botSkillService';

jest.mock('@/services/backendApi/bots/botSkillController', () => ({
  listBotSkillsByBot: jest.fn(),
}));
jest.mock('@/services/backendApi/tasks/taskConfigController', () => ({
  matchTaskConfig: jest.fn(),
}));

const mocked = jest.requireMock('@/services/backendApi/bots/botSkillController') as {
  listBotSkillsByBot: jest.Mock;
};
const mockedMatch = jest.requireMock('@/services/backendApi/tasks/taskConfigController') as {
  matchTaskConfig: jest.Mock;
};

function skillsEnvelope(items: Array<{ skill_id: string; name: string; active: boolean }>) {
  return { code: 200000, message: 'OK', data: { items, total: items.length, page: 1, page_size: 100 } };
}

function matchEnvelope(matched: boolean) {
  return { code: 200000, message: 'OK', data: { matched } };
}

describe('botSkillService.hasTaskIntentSkillForBot', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('third_party Bot 不走 Skills 列表/任务配置探针,直接判不可用', async () => {
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'bot_external',
      botUuid: 'bot_external',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({ ok: true, data: false });
    expect(mocked.listBotSkillsByBot).not.toHaveBeenCalled();
    expect(mockedMatch.matchTaskConfig).not.toHaveBeenCalled();
  });

  it('属主 bot(combotId 无 owner 尾)走技能列表,命中 task-intake → true', async () => {
    mocked.listBotSkillsByBot.mockResolvedValue(
      skillsEnvelope([{ skill_id: 's1', name: 'task-intake', active: true }]),
    );
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'bot-main',
      botUuid: 'bot-main',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({ ok: true, data: true });
    expect(mocked.listBotSkillsByBot).toHaveBeenCalledWith(
      'bot-main',
      expect.objectContaining({ user_id: 'user-1' }),
    );
    expect(mockedMatch.matchTaskConfig).not.toHaveBeenCalled();
  });

  it('属主 bot(ownerId 等于当前用户)也走技能列表,未命中 → false', async () => {
    mocked.listBotSkillsByBot.mockResolvedValue(skillsEnvelope([]));
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'botA',
      botUuid: 'botA:user-1',
      ownerId: 'user-1',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({ ok: true, data: false });
    expect(mockedMatch.matchTaskConfig).not.toHaveBeenCalled();
  });

  it('非属主 bot 走 match 探针,传 task_skill_installed_bots + realBotId,只回布尔', async () => {
    mockedMatch.matchTaskConfig.mockResolvedValue(matchEnvelope(true));
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'botX',
      botUuid: 'botX:other-user',
      ownerId: 'other-user',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({ ok: true, data: true });
    expect(mockedMatch.matchTaskConfig).toHaveBeenCalledWith({
      config_name: TASK_INTENT_INSTALLED_BOTS_CONFIG,
      value: 'botX:other-user',
    });
    expect(mocked.listBotSkillsByBot).not.toHaveBeenCalled();
  });

  it('非属主 bot 探针未命中 → false', async () => {
    mockedMatch.matchTaskConfig.mockResolvedValue(matchEnvelope(false));
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'botX',
      botUuid: 'botX:other-user',
      ownerId: 'other-user',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({ ok: true, data: false });
  });

  it('match 接口失败信封 → DomainResult error,不向上抛', async () => {
    mockedMatch.matchTaskConfig.mockResolvedValue({ code: 500000, message: 'boom', data: undefined });
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'botX',
      botUuid: 'botX:other-user',
      ownerId: 'other-user',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({ ok: false, error: expect.objectContaining({ code: 'BOT_SKILL_FAILED' }) });
  });

  it('技能列表请求异常 → DomainResult error,不向上抛', async () => {
    mocked.listBotSkillsByBot.mockRejectedValueOnce(new Error('Not Found'));
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'bot-main',
      botUuid: 'bot-main',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({
      ok: false,
      error: {
        code: 'BOT_SKILL_FAILED',
        friendlyMessage: 'Not Found',
        canRetry: true,
      },
    });
  });

  it('match 请求异常 → DomainResult error,不向上抛', async () => {
    mockedMatch.matchTaskConfig.mockRejectedValueOnce(new Error('Not Found'));
    const res = await botSkillService.hasTaskIntentSkillForBot({
      realBotId: 'botX',
      botUuid: 'botX:other-user',
      ownerId: 'other-user',
      currentUserId: 'user-1',
    });
    expect(res).toEqual({
      ok: false,
      error: {
        code: 'BOT_SKILL_FAILED',
        friendlyMessage: 'Not Found',
        canRetry: true,
      },
    });
  });
});
