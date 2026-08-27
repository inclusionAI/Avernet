/** @jest-environment jsdom */
import { buildGroupChatBridgeRequest } from '@/pages/Workspace/hooks/groupChatRequestBuilder';
import type { GroupChatRequest } from '@/services/workspace/groupChatProvider';
import {
  buildGroupChatPayload,
  resolveGroupGatewayOrigin,
  resolveGroupWsOrigin,
} from '@/services/workspace/groupChatProviderHelpers';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';

/**
 * 群聊 ws 帧 payload 装配单测——根因 A（query ← content）+ C（botUuid 兜底/覆盖、字段保真）。
 *
 * 真链路数据级验证：bridge.sendMessage(content) → buildGroupChatBridgeRequest（顶层 content）
 *   → provider.request(params) → buildGroupChatPayload(params) → GroupChatInput（query=params.content）。
 * 此组合证 ws 帧 message 字段非空（根因 A 修复）+ bot_uuid/mentionAll 等保真（根因 C 修复）。
 */
describe('buildGroupChatPayload — 根因 A query←content + C botUuid/字段保真', () => {
  const groupId = 'g1';
  const identityId = 'me';

  it('query ← params.content（根因 A：ws 帧 message 非空）', () => {
    const params: GroupChatRequest = { content: '杭州天气', sessionId: 's1' };
    expect(buildGroupChatPayload(params, groupId, identityId).query).toBe('杭州天气');
  });

  it('botUuid 缺省时 toHumanBotUuid(identityId) 兜底（直接路径不回归）', () => {
    const params: GroupChatRequest = { content: 'hi', sessionId: 's1' };
    expect(buildGroupChatPayload(params, groupId, identityId).botUuid).toBe('human_me');
  });

  it('botUuid 显式传入时覆盖兜底（桥 @指定 bot 路由正确,根因 C）', () => {
    const params: GroupChatRequest = { content: 'hi', sessionId: 's1', botUuid: 'bot-xyz' };
    expect(buildGroupChatPayload(params, groupId, identityId).botUuid).toBe('bot-xyz');
  });

  it('mentionAll/replyToMessageId/mentions/panelContext/isInject 保真透传', () => {
    const params: GroupChatRequest = {
      content: 'hi',
      sessionId: 's1',
      mentionAll: true,
      replyToMessageId: 'm1',
      mentions: ['bot-a'],
      panelContext: { foo: 1 } as never,
      isInject: true,
    };
    const p = buildGroupChatPayload(params, groupId, identityId);
    expect(p.mentionAll).toBe(true);
    expect(p.replyToMessageId).toBe('m1');
    expect(p.mentions).toEqual(['bot-a']);
    expect(p.panelContext).toEqual({ foo: 1 });
    expect(p.isInject).toBe(true);
  });

  it('真链路组合：buildGroupChatBridgeRequest → buildGroupChatPayload 的 query === 原始 content', () => {
    // 模拟 aixcore 卡片 bridge.sendMessage('杭州天气', { botUuid: 'bot-xyz' })
    const bridgeParams = buildGroupChatBridgeRequest('s1', '杭州天气', { botUuid: 'bot-xyz' });
    const payload = buildGroupChatPayload(bridgeParams, groupId, identityId);
    expect(payload.query).toBe('杭州天气'); // ws 帧 message 非空（根因 A 闭环）
    expect(payload.botUuid).toBe('bot-xyz'); // 桥 @指定 bot 透传（根因 C 闭环）
    expect(payload.groupId).toBe(groupId);
    expect(payload.senderId).toBeDefined();
  });
});

/**
 * resolveGroupWsOrigin 单测——部署态 WSS 直连网关绕过 tern cors proxy 的 WS 盲区。
 *
 * 策略验证：
 * - localhost / 127.0.0.1 / 空 → undefined（走 dev-server proxy 同源拼接）
 * - renderoffice-pre hostname → wss://{pre-gateway}
 * - renderoffice（无后缀）hostname → wss://{prod-gateway}
 * - define 未注入 → undefined（typeof 守卫不 ReferenceError）
 */
describe('resolveGroupWsOrigin — 已注入 define 常量', () => {
  beforeEach(() => {
    // 模拟 Umi define 编译期注入的全局常量
    (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PRE = 'https://gateway-pre.example.com';
    (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PROD = 'https://gateway.example.com';
  });

  afterEach(() => {
    delete (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PRE;
    delete (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PROD;
  });

  it('localhost 返回 undefined（走 dev-server proxy 同源拼接）', () => {
    expect(resolveGroupWsOrigin('localhost')).toBeUndefined();
  });

  it('127.0.0.1 返回 undefined（走 dev-server proxy 同源拼接）', () => {
    expect(resolveGroupWsOrigin('127.0.0.1')).toBeUndefined();
  });

  it('空字符串返回 undefined（SSR / 无 window 场景）', () => {
    expect(resolveGroupWsOrigin('')).toBeUndefined();
  });

  it('renderoffice PRE hostname 返回 wss:// pre 网关 origin', () => {
    expect(resolveGroupWsOrigin('app-pre.example.com')).toBe('wss://gateway-pre.example.com');
  });

  it('renderoffice PROD hostname（无 -pre/-dev 后缀）返回 wss:// prod 网关 origin', () => {
    expect(resolveGroupWsOrigin('app.example.com')).toBe('wss://gateway.example.com');
  });
});

describe('resolveGroupWsOrigin — define 未注入（typeof 守卫不 ReferenceError）', () => {
  it('非 localhost hostname 在 define 未注入时返回 undefined', () => {
    expect(resolveGroupWsOrigin('app-pre.example.com')).toBeUndefined();
  });
});

describe('resolveGroupGatewayOrigin — 资源标签直连网关', () => {
  beforeEach(() => {
    (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PRE = 'https://gateway-pre.example.com';
    (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PROD = 'https://gateway.example.com';
  });

  afterEach(() => {
    delete (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PRE;
    delete (globalThis as Record<string, unknown>).TEAMCLAW_WS_GW_PROD;
  });

  it('localhost 返回 undefined（资源标签保持同源代理）', () => {
    expect(resolveGroupGatewayOrigin('localhost')).toBeUndefined();
  });

  it('renderoffice PRE/DEV hostname 返回 https pre 网关', () => {
    expect(resolveGroupGatewayOrigin('app-pre.example.com')).toBe(
      'https://gateway-pre.example.com',
    );
    expect(resolveGroupGatewayOrigin('app-dev.example.com')).toBe(
      'https://gateway-pre.example.com',
    );
  });

  it('renderoffice PROD hostname 返回 https prod 网关', () => {
    expect(resolveGroupGatewayOrigin('app.example.com')).toBe(
      'https://gateway.example.com',
    );
  });
});
