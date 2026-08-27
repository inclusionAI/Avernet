/** @jest-environment jsdom */
// @jest/globals 必须先于被测模块导入：jest.mock 工厂在执行时会引用这里的 jest 绑定，
// 若被测模块先加载会触发工厂求值，此时 jest 绑定尚未初始化（undefined.jest 报错）。
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import * as botSessionService from '@/services/workspace/botSessionService';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
// auto-mock(不带 factory),避免在 hoisted factory 内引用 jest.fn() —— 与 @jest/globals 一起会触发 TDZ。
// initialize()/request() 都会拉 IAM token,这里 stub 成固定值,避免打到真实 /api/v1/token/iam。
jest.mock('@/services/backendApi/privateChat/iamTokenController');
// SDK 包以 ESM 发布,jest+babel-node 不转译 node_modules——这里用 factory 闭包捕获 mockSdk
// 变量(同 useGroupChat.test / groupChatProvider.test 约定)。真实集成由 fakeSdkProvider 注入。
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

import { createBotChatProvider } from '@/services/workspace/botChatProvider';
import * as adapters from '@tc-chat/adapters';

const mockedGetConnection = (ctrl as any).getBotConnection as jest.Mock<any>;
const mockedGetIamToken = getBotIamToken as jest.Mock<any>;

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(botSessionService.botSessionService, 'listMessages').mockResolvedValue([]);
  mockSdk.isConnected = false;
  mockSdk.connect.mockResolvedValue(undefined);
  mockSdk.request.mockResolvedValue(undefined);
  mockSdk.subscribeToConnectionStatus.mockReturnValue(jest.fn());
  // auto-mock 默认返回 undefined,补上默认 IAM token,供 initialize()/request() 消费。
  mockedGetIamToken.mockResolvedValue('iam-token');
});

afterEach(() => {
  jest.restoreAllMocks();
});

const bot = {
  botId: 'b:2088',
  realBotId: 'b',
  ownerId: '2088',
  displayName: 'B',
  online: true,
  chatable: true,
  runtimeStage: 'online' as const,
};

describe('botChatProvider', () => {
  it('initialize 调用 getBotConnection 并把 sockets[chat].url 传给 SDK,sessionKey=sessionId', async () => {
    mockedGetConnection.mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws?token=1' }] },
      message: 'OK',
      request_id: 'r',
    });
    const p = createBotChatProvider({ bot, userId: 'u', sessionId: 'sid-1' });
    await p.connect();
    expect(mockedGetConnection).toHaveBeenCalledWith('b', { user_id: 'u', owner_id: '2088' });
    const cfg = (adapters.OpenClawProvider as jest.Mock<any>).mock.calls[0][0] as {
      url: string;
      sessionKey: string;
    };
    // 连接接口返回的是带握手 token 的完整地址，Provider 应原样交给 SDK。
    expect(cfg.url).toBe('wss://gw/ws?token=1');
    expect(cfg.sessionKey).toBe('sid-1');
  });

  it('initialize 拉 IAM token 并注入 SDK(x-iam-token 与 url 内含的连接 token 是两套凭证)', async () => {
    mockedGetConnection.mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws?token=1' }] },
      message: 'OK',
      request_id: 'r',
    });
    const p = createBotChatProvider({ bot, userId: 'u', sessionId: 'sid-1' });
    await p.connect();
    const cfg = (adapters.OpenClawProvider as jest.Mock<any>).mock.calls[0][0] as {
      xIAMToken?: string;
      credentialProvider?: () => Promise<{ xIAMToken?: string; xProxypassToken?: string }>;
    };
    expect(mockedGetIamToken).toHaveBeenCalledTimes(1);
    expect(mockedGetIamToken).toHaveBeenCalledWith('b', 'u', '2088', 'online');
    expect(cfg.xIAMToken).toBe('iam-token');
    expect(typeof cfg.credentialProvider).toBe('function');
    // 重连刷新只返回 xIAMToken,不返回 xProxypassToken——避免 SDK 给内含 token 的 url 追加查询参数。
    mockedGetIamToken.mockResolvedValue('iam-refreshed');
    const creds = await cfg.credentialProvider!();
    expect(creds).toEqual({ xIAMToken: 'iam-refreshed' });
    expect(creds.xProxypassToken).toBeUndefined();
  });

  it('调试草稿 Bot 的 IAM token 在初始化、重连刷新和发送前均使用 draft stage', async () => {
    mockedGetConnection.mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws?token=1' }] },
      message: 'OK',
      request_id: 'r',
    });
    const draftBot = { ...bot, runtimeStage: 'draft' as const };
    const p = createBotChatProvider({ bot: draftBot, userId: 'u', sessionId: 'sid-1' });
    await p.connect();
    const cfg = (adapters.OpenClawProvider as jest.Mock<any>).mock.calls[0][0] as {
      credentialProvider?: () => Promise<{ xIAMToken?: string }>;
    };
    await cfg.credentialProvider!();
    mockSdk.isConnected = true;
    await p.request({ content: 'hi', sessionId: 'sid-1' });

    expect(mockedGetIamToken).toHaveBeenCalledTimes(3);
    expect(mockedGetIamToken).toHaveBeenNthCalledWith(1, 'b', 'u', '2088', 'draft');
    expect(mockedGetIamToken).toHaveBeenNthCalledWith(2, 'b', 'u', '2088', 'draft');
    expect(mockedGetIamToken).toHaveBeenNthCalledWith(3, 'b', 'u', '2088', 'draft');
  });

  it('getChatUrl 对 human_ 前缀的 userId 只传工号(回归:连接接口不再带 human_ 前缀)', async () => {
    mockedGetConnection.mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws?token=1' }] },
      message: 'OK',
      request_id: 'r',
    });
    const p = createBotChatProvider({ bot, userId: 'human_327325', sessionId: 'sid-1' });
    await p.connect();
    expect(mockedGetConnection).toHaveBeenCalledWith('b', { user_id: '327325', owner_id: '2088' });
  });

  it('request 透传 query + sessionKey,并按需 connect', async () => {
    mockedGetConnection.mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws' }] },
      message: 'OK',
      request_id: 'r',
    });
    const p = createBotChatProvider({ bot, userId: 'u', sessionId: 'sid-1' });
    await p.connect();
    mockSdk.isConnected = true;
    await p.request({ content: 'hi', sessionId: 'sid-1' });
    // connect 已拉一次;request 发送前再刷新一次,确保请求帧携带最新 x-iam-token。
    expect(mockedGetIamToken).toHaveBeenCalledTimes(2);
    expect(mockSdk.request).toHaveBeenCalledWith({ query: 'hi', sessionKey: 'sid-1' }, undefined);
  });

  it('request 透传 resourceReferences 与 promptFileRefs', async () => {
    mockedGetConnection.mockResolvedValue({
      code: 200000,
      data: { engine: 'openclaw', expires_at: 'x', sockets: [{ kind: 'chat', url: 'wss://gw/ws' }] },
      message: 'OK',
      request_id: 'r',
    });
    const p = createBotChatProvider({ bot, userId: 'u', sessionId: 'sid-1' });
    await p.connect();
    mockSdk.isConnected = true;
    await p.request({
      content: '看下这个',
      sessionId: 'sid-1',
      resourceReferences: [{ type: 'file', resource_id: 'sr_1', insert_id: 'ins_1' }],
      promptFileRefs: [{ resource_id: 'sr_1', insert_id: 'ins_1' }],
    });
    expect(mockSdk.request).toHaveBeenCalledWith(
      {
        query: '看下这个',
        sessionKey: 'sid-1',
        resourceReferences: [{ type: 'file', resource_id: 'sr_1', insert_id: 'ins_1' }],
        promptFileRefs: [{ resource_id: 'sr_1', insert_id: 'ins_1' }],
      },
      undefined,
    );
  });

  it('连接/请求失败转为 error 阶段', async () => {
    mockedGetConnection.mockRejectedValue(new Error('conn boom'));
    const p = createBotChatProvider({ bot, userId: 'u', sessionId: 'sid-1' });
    await expect(p.connect()).rejects.toThrow('conn boom');
  });

  it('loadHistory 委托 botSessionService.listMessages', async () => {
    (botSessionService.botSessionService.listMessages as jest.Mock<any>).mockResolvedValue([
      { id: 'm1', role: 'user', content: 'hi', status: 'history', blocks: [] },
    ]);
    const p = createBotChatProvider({ bot, userId: 'u', sessionId: 'sid-1' });
    const out = await p.loadHistory();
    expect(botSessionService.botSessionService.listMessages).toHaveBeenCalledWith(bot, 'u', 'sid-1');
    expect(out).toHaveLength(1);
  });
});
