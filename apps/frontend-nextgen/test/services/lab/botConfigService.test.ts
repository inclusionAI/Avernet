import type { BotConfigGateway } from '@/services/lab';
import { BROWSE_NOTE_MAX_LENGTH, BotConfigService } from '@/services/lab';

// Unified 面 §3：触发模式由后端固定 openclaw（B 方案），前端不再发送 mode；input 仅 {botId, ownerUserId, note}。

function makeGateway(): jest.Mocked<BotConfigGateway> {
  return {
    listSubscriptions: jest.fn(async () => [{ botId: 'bot-001', ownerUserId: 'u1', mode: 'openclaw', note: 'x' }]),
    upsertSubscription: jest.fn(async (input) => ({
      botId: input.botId,
      ownerUserId: input.ownerUserId,
      mode: 'openclaw',
      note: input.note ?? null,
    })),
    deleteSubscription: jest.fn(async () => undefined),
  } as unknown as jest.Mocked<BotConfigGateway>;
}

describe('BotConfigService', () => {
  it('listSubscriptions 透传 Gateway（owner_user_id 必填）', async () => {
    const service = new BotConfigService(makeGateway());
    const subs = await service.listSubscriptions('u1');
    expect(subs).toHaveLength(1);
  });

  it('enableSubscription 缺 Bot/owner 抛错，不下发 upsert', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    await expect(service.enableSubscription('', 'u1')).rejects.toThrow('缺少 Bot');
    await expect(service.enableSubscription('b1', '')).rejects.toThrow('缺少当前用户身份');
    expect(gw.upsertSubscription).not.toHaveBeenCalled();
  });

  it('enable 不发送 mode（后端固定）；备注 trim 后下发', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    const result = await service.enableSubscription('b1', 'u1', '  hi  ');
    expect(gw.upsertSubscription).toHaveBeenCalledTimes(1);
    expect(gw.upsertSubscription).toHaveBeenCalledWith({ botId: 'b1', ownerUserId: 'u1', note: 'hi' }, undefined);
    expect(result.note).toBe('hi');
  });

  it('enable 空/纯空格 note 归一 null', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    await service.enableSubscription('b1', 'u1', '   ');
    expect(gw.upsertSubscription.mock.calls[0][0].note).toBeNull();
    await service.enableSubscription('b1', 'u1', undefined);
    expect(gw.upsertSubscription.mock.calls[1][0].note).toBeNull();
  });

  it('updateNote 复用同一 upsert（mode 不进 input），用于已开启行的备注保存', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    await service.updateNote('b1', 'u1', 'note2');
    expect(gw.upsertSubscription).toHaveBeenCalledWith({ botId: 'b1', ownerUserId: 'u1', note: 'note2' }, undefined);
  });

  it('disableSubscription 缺 Bot 抛错，不下发 delete', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    await expect(service.disableSubscription('')).rejects.toThrow('缺少 Bot');
    expect(gw.deleteSubscription).not.toHaveBeenCalled();
  });

  it('disableSubscription 转发 Gateway.deleteSubscription', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    await service.disableSubscription('b1');
    expect(gw.deleteSubscription).toHaveBeenCalledWith('b1', undefined);
  });

  it('note 超长按上限截断（对齐后端 512）', async () => {
    const gw = makeGateway();
    const service = new BotConfigService(gw);
    const long = 'a'.repeat(BROWSE_NOTE_MAX_LENGTH + 10);
    await service.enableSubscription('b1', 'u1', long);
    expect(gw.upsertSubscription.mock.calls[0][0].note?.length).toBe(BROWSE_NOTE_MAX_LENGTH);
  });
});
