/** @jest-environment jsdom */
/**
 * A2A connect 失败回退 v3（review P3 行为测试——补缝钉板不能证明行为的空白）。
 *
 * 场景：传输选择缓存已收敛 'a2a'（takeCached 命中）→ A2A Provider 的
 * connect() 在发送任何请求之前失败（连接阶段探测再失败）：
 *   ① 选择必须回退 probe-v3（改回选择状态，下次工厂构建走 v3）；
 *   ② 失败的 A2A inner 不得滞留缓存（否则下次 connect 复用死 Provider，
 *     v3 本可用却持续失败）。
 * 已出站请求的失败不进此路（归 provider 恢复/取消面，不重发）——见
 * botChatProvider.connectAtEpoch 回退分支注释。
 *
 * mock 口径沿用 botChatProvider.test.ts（jsdom + factory 闭包捕获 mockSdk；
 * @jest/globals TDZ 约定见该文件头注）。
 */
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import * as botSessionService from '@/services/workspace/botSessionService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/privateChat/iamTokenController');

const mockSdk: any = {
  onMessage: undefined,
  onComplete: undefined,
  onError: undefined,
  isConnected: false,
  connect: jest.fn(),
  disconnect: jest.fn(),
  request: jest.fn(),
  abort: jest.fn(),
  subscribeToConnectionStatus: jest.fn(() => jest.fn()),
};
jest.mock('@tc-chat/adapters', () => ({
  OpenClawProvider: jest.fn(() => mockSdk),
}));

// bootstrap 缝面手写最小 mock（TDZ：@jest/globals 下 hoisted 工厂禁止
// requireActual/jest.*，仅惰性桥接 mock 前缀变量）；isSendBannedForSession
// 本测试面恒 false（发送门行为有专测）。
const mockA2a = { takeCached: jest.fn(), revert: jest.fn() };
jest.mock('@/adapters/a2a/bootstrap', () => ({
  isSendBannedForSession: () => false,
  takeCachedA2AProvider: (...args: any[]) => mockA2a.takeCached(...args),
  revertA2AChoice: (...args: any[]) => mockA2a.revert(...args),
}));

import { createBotChatProvider } from '@/services/workspace/botChatProvider';
import * as adapters from '@tc-chat/adapters';

const mockedGetConnection = (ctrl as any).getBotConnection as jest.Mock<any>;
const mockedGetIamToken = getBotIamToken as jest.Mock<any>;

const bot = {
  botId: 'b:2088', realBotId: 'b', ownerId: '2088', displayName: 'B',
  online: true, chatable: true, runtimeStage: 'online' as const,
};

const connectionOk = () => ({
  code: 200000,
  message: 'OK', request_id: 'r',
  data: {
    engine: 'openclaw', expires_at: 'x',
    sockets: [{ kind: 'chat', url: 'wss://gw/ws?token=1' }],
  },
});

const failingA2aConnect = () => ({
  isA2aProvider: true as const,
  onMessage: undefined, onComplete: undefined, onError: undefined,
  isConnected: false,
  // 同步 throw 而非 mockRejectedValue：拒绝处不留浮动 rejected promise
  //（jest 对 mockRejectedValue 的浮动拒绝按 unhandled 记账，污染本用例）
  connect: jest.fn(() => { throw new Error('a2a probe failed: network'); }),
  disconnect: jest.fn(),
  request: jest.fn(),
  abort: jest.fn(),
  subscribeToConnectionStatus: jest.fn(() => jest.fn()),
});

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(botSessionService.botSessionService, 'listMessagesPage')
    .mockResolvedValue({ messages: [], total: 0, rawCount: 0 });
  mockedGetConnection.mockResolvedValue(connectionOk());
  mockedGetIamToken.mockResolvedValue('iam-token');
  mockSdk.isConnected = false;
  mockSdk.connect.mockResolvedValue(undefined);
  mockSdk.connect.mockClear();
});

describe('botChatProvider A2A connect 失败回退（P3 行为面）', () => {
  it('缓存命中 a2a 但 connect 探测失败 → 回退选择 probe-v3 + 丢弃 inner，下次 connect 落 v3 成功',
    async () => {
      const a2aInner = failingA2aConnect();
      mockA2a.takeCached
        .mockReturnValueOnce(a2aInner as any)   // 第一次工厂：缓存命中 a2a
        .mockReturnValueOnce(null);              // 回退后：probe-v3 → v3
      const provider = createBotChatProvider({ bot, userId: 'u', sessionId: 's1' });

      // 第一次 connect：A2A 连接阶段失败如实上抛
      await expect(provider.connect()).rejects.toThrow('a2a probe failed');
      // ① 选择回退被调（选择状态变更归属 bootstrap，此处钉住宿主必调回退缝）
      expect(mockA2a.revert).toHaveBeenCalledTimes(1);
      // ② 失败 inner 不滞留：显式断连
      expect(a2aInner.disconnect).toHaveBeenCalled();

      // 第二次 connect：工厂重建 → 落 v3 → 连接成功（不允许"v3 可用却持续失败"）
      await provider.connect();
      expect(adapters.OpenClawProvider).toHaveBeenCalled();
      expect(mockSdk.connect).toHaveBeenCalledTimes(1);
    });

  it('v3 自身的 connect 失败不触发 A2A 回退缝（回退仅限 A2A inner）', async () => {
    mockA2a.takeCached.mockReturnValueOnce(null);
    mockSdk.connect.mockRejectedValueOnce(new Error('v3 down'));
    const provider = createBotChatProvider({ bot, userId: 'u', sessionId: 's1' });
    await expect(provider.connect()).rejects.toThrow('v3 down');
    expect(mockA2a.revert).not.toHaveBeenCalled();
  });
});