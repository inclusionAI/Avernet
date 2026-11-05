/**
 * Bot 复制前端契约对齐测试(对应 feat/bot-copy-contract):
 * - `botWorkshopService.copyBot` 请求体必须严格匹配后端 `BotCopyRequest`(extra="forbid"):
 *   excludes 进 `request_detail`,avatar_url / 顶层 excludes 不得出现。
 * - 响应按 `type` 判别:success → {type:'issued'}(无 BotDto);auth_required → 授权面板
 *   形状(无 request/agentCoding,因复制无 auth-status 轮询端点)。
 * - `retryCopyBot` 映射 {operation_id, result}。
 * - `mapBotDto` 的 copy_state 镜像:合法状态→对象,null→null,缺省/越界→undefined。
 */

import { copyBot as copyBotApi, retryCopyBot as retryCopyBotApi } from '@/services/backendApi/bots/botController';
import { mapBotDto } from '@/services/bots/botMapper';
import { botWorkshopService } from '@/services/botWorkshop/botWorkshopService';
import type { BotCopyInput } from '@/services/botWorkshop';

jest.mock('@/services/backendApi/bots/botController', () => ({
  copyBot: jest.fn(),
  retryCopyBot: jest.fn(),
  // 其余导出在 copy/retry 路径用不到,置 undefined 即可。
}));
jest.mock('@/services/botWorkshop/botAvatarService', () => ({
  botAvatarService: { save: jest.fn().mockResolvedValue('avatar-url'), get: jest.fn() },
}));

const copyBotApiMock = copyBotApi as jest.Mock;
const retryCopyBotApiMock = retryCopyBotApi as jest.Mock;

const baseInput = (over: Partial<BotCopyInput> = {}): BotCopyInput => ({
  sourceBotId: 'src_1',
  sourceOwnerId: 'u1',
  name: '副本',
  description: 'desc',
  engine: 'openclaw',
  spaceId: 'sp_1',
  ownership: 'personal',
  serviceMode: 'non-service',
  excludes: ['/x', '/y'],
  ...over,
});

function envelope(data: unknown) {
  return { data, ok: true, code: 0 };
}

describe('botWorkshopService.copyBot — 请求体契约', () => {
  beforeEach(() => copyBotApiMock.mockReset());

  test('excludes 进 request_detail,不出现顶层 excludes / avatar_url', async () => {
    copyBotApiMock.mockResolvedValueOnce(
      envelope({ type: 'success', operation_id: 'op_1', bot_id: 'dst_1' }),
    );
    await botWorkshopService.copyBot(baseInput({ avatarUrl: 'data:avatar' }));
    const body = copyBotApiMock.mock.calls[0][0];
    expect(body).toEqual({
      source_bot_id: 'src_1',
      bot_name: '副本',
      bot_desc: 'desc',
      request_detail: { excludes: ['/x', '/y'] },
    });
    expect(body).not.toHaveProperty('avatar_url');
    expect(body).not.toHaveProperty('excludes');
  });

  test('excludes 为空时不发 request_detail', async () => {
    copyBotApiMock.mockResolvedValueOnce(
      envelope({ type: 'success', operation_id: 'op_1', bot_id: 'dst_1' }),
    );
    await botWorkshopService.copyBot(baseInput({ excludes: [] }));
    const body = copyBotApiMock.mock.calls[0][0];
    expect(body).not.toHaveProperty('request_detail');
  });
});

describe('botWorkshopService.copyBot — 响应判别', () => {
  beforeEach(() => copyBotApiMock.mockReset());

  test('type=success → {type:"issued", botId, operationId}(无 BotDto)', async () => {
    copyBotApiMock.mockResolvedValueOnce(
      envelope({ type: 'success', operation_id: 'op_1', bot_id: 'dst_1' }),
    );
    const result = await botWorkshopService.copyBot(baseInput());
    expect(result).toEqual({ type: 'issued', botId: 'dst_1', operationId: 'op_1' });
  });

  test('type=auth_required → 与创建同款授权视图:携带 request(供 auth-status 轮询),不含 agentCoding', async () => {
    copyBotApiMock.mockResolvedValueOnce(
      envelope({
        type: 'auth_required',
        operation_id: 'op_2',
        bot_id: 'dst_2',
        iframe_url: 'https://agentpass/x',
        redirect_url: 'https://agentpass/r',
      }),
    );
    const result = await botWorkshopService.copyBot(baseInput({ avatarUrl: 'data:avatar' }));
    expect(result.type).toBe('authorization_required');
    if (result.type !== 'authorization_required') throw new Error('unreachable');
    expect(result.botId).toBe('dst_2');
    expect(result.iframeUrl).toBe('https://agentpass/x');
    expect(result.redirectUrl).toBe('https://agentpass/r');
    expect(result.avatarUrl).toBe('data:avatar');
    // request 用于复用 useBotCreateAuthorization 轮询 dst bot 的 auth-status(与创建一致)。
    expect(result.request).toEqual({
      bot_name: '副本',
      bot_desc: 'desc',
      engine: 'openclaw',
      cluster_name: 'ACRA',
      bot_type: 'personal',
    });
    expect(result).not.toHaveProperty('agentCoding');
  });

  test('type 缺失/异常 → 抛错', async () => {
    copyBotApiMock.mockResolvedValueOnce(envelope({ bot_id: 'dst_3' }));
    await expect(botWorkshopService.copyBot(baseInput())).rejects.toThrow('复制接口返回结构异常');
  });
});

describe('botWorkshopService.retryCopyBot', () => {
  beforeEach(() => retryCopyBotApiMock.mockReset());

  test('映射 {operation_id, result}', async () => {
    retryCopyBotApiMock.mockResolvedValueOnce(
      envelope({ operation_id: 'op_1', result: 're_enqueued' }),
    );
    const result = await botWorkshopService.retryCopyBot('dst_1');
    expect(result).toEqual({ operationId: 'op_1', result: 're_enqueued' });
    expect(retryCopyBotApiMock).toHaveBeenCalledWith('dst_1');
  });

  test('结构异常 → 抛错', async () => {
    retryCopyBotApiMock.mockResolvedValueOnce(envelope({ result: 'terminal' }));
    await expect(botWorkshopService.retryCopyBot('dst_1')).rejects.toThrow('重试接口返回结构异常');
  });
});

describe('mapBotDto — copy_state 镜像', () => {
  const baseDto = {
    bot_id: 'dst_1',
    bot_name: 'n',
    engine_type: 'openclaw',
    bot_type: 'personal',
    status: 'ACTIVE',
    owner_entity_id: 'u1',
  } as Record<string, unknown>;

  test('合法 copy_state → 镜像(操作 id/状态/source)', () => {
    const dto = {
      ...baseDto,
      copy_state: { operation_id: 'op_1', status: 'AUTH_PENDING', source_bot_id: 'src_1' },
    };
    const { item } = mapBotDto(dto);
    expect(item.copyState).toEqual({
      operationId: 'op_1',
      status: 'AUTH_PENDING',
      sourceBotId: 'src_1',
    });
  });

  test('copy_state=null → null(CONVERGED 收敛后)', () => {
    const dto = { ...baseDto, copy_state: null };
    const { item } = mapBotDto(dto);
    expect(item.copyState).toBeNull();
  });

  test('缺省 copy_state → undefined(非复制 bot)', () => {
    const { item } = mapBotDto(baseDto);
    expect(item.copyState).toBeUndefined();
  });

  test('status 越界 → undefined(不渲染半残徽标)', () => {
    const dto = {
      ...baseDto,
      copy_state: { operation_id: 'op_1', status: 'BOGUS', source_bot_id: 'src_1' },
    };
    const { item } = mapBotDto(dto);
    expect(item.copyState).toBeUndefined();
  });
});
