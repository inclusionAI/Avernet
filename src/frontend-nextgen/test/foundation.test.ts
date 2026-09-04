import access from '../src/access';
import { resolveDefaultBotCapabilityProfile } from '../src/adapters/bot-capability';
import { resolveBotRuntime } from '../src/adapters/bot-runtime';
import { BOT_ENDPOINTS, CHAT_MESSAGE_ENDPOINTS, COLLABORATION_GROUP_ENDPOINTS } from '../src/services/backendApi';
import { getRouteMeta } from '../src/shell/routeMeta';

describe('foundation architecture helpers', () => {
  test('通过显式 route meta 识别二级路由归属', () => {
    // Open Core 形态下 /market/mcp/* 已剥离到 internal overlay，基线不收录
    expect(getRouteMeta('/market/mcp/detail')).toBeUndefined();
    expect(getRouteMeta('/collaboration-square/bots')?.section).toBe('work');
    expect(getRouteMeta('/bot-workshop/logs')?.navKey).toBe('bot-workshop');
    expect(getRouteMeta('/bot-workshop/logs')?.title).toBe('Bot 日志');
  });

  test('BotRuntime 解析默认 Bot 并保留引擎主维度', () => {
    expect(resolveBotRuntime({ engine: 'OpenClaw', botId: 'default-assistant' })).toEqual({
      engine: 'OpenClaw',
      templateType: undefined,
      botType: undefined,
      isAgentCodingBot: false,
      isDefaultBot: true,
    });
  });

  test('普通 CC 模板不识别为 AgentCoding Bot', () => {
    expect(
      resolveBotRuntime({ engine: 'claude_code', templateType: 'normal', botId: 'normal-cc' }).isAgentCodingBot,
    ).toBe(false);
    expect(
      resolveBotRuntime({ engine: 'claude_code', templateType: 'normalCC', botId: 'normal-cc-2' }).isAgentCodingBot,
    ).toBe(false);
    expect(resolveBotRuntime({ engine: 'claude_code', botId: 'normal-cc-3' }).isAgentCodingBot).toBe(false);
    expect(
      resolveBotRuntime({ engine: 'claude_code', templateType: 'generalCC', botId: 'agent-coding-general' })
        .isAgentCodingBot,
    ).toBe(true);
    expect(
      resolveBotRuntime({ engine: 'claude_code', templateType: 'applicationCoding', botId: 'agent-coding' })
        .isAgentCodingBot,
    ).toBe(true);
  });

  test('未知引擎能力显式降级', () => {
    const profile = resolveDefaultBotCapabilityProfile(resolveBotRuntime({}));

    expect(profile.canPublish).toBe(false);
    expect(profile.unsupportedReasons.publish).toBe('当前 Bot 引擎未识别，暂不支持发布');
  });

  test('权限初始化状态缺失时默认放行，避免本地开发白屏', () => {
    expect(access()).toEqual({ canUseWorkspace: true });
    expect(access({})).toEqual({ canUseWorkspace: true });
    expect(access({ currentUser: { id: 'user-1' } })).toEqual({ canUseWorkspace: true });
    expect(access({ currentUser: { id: '' } })).toEqual({ canUseWorkspace: false });
  });
});

describe('backendApi Controller 协议层', () => {
  test('按后端接口域维护 Bot / Chat / Collaboration endpoint', () => {
    expect(BOT_ENDPOINTS.detail('bot-1')).toBe('/openapi/v1/bots/bot-1');
    expect(CHAT_MESSAGE_ENDPOINTS.detail('msg-1')).toBe('/openapi/v1/chat/messages/msg-1');
    expect(COLLABORATION_GROUP_ENDPOINTS.participant('group-1', 'actor-1')).toBe(
      '/openapi/v1/collaboration/groups/group-1/participants/actor-1',
    );
  });
});
