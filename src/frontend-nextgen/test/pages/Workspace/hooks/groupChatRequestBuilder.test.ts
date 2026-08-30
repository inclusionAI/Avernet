/** @jest-environment jsdom */
import {
  buildEchoAttachments,
  buildGroupChatBridgeRequest,
  buildGroupUserMessageExtra,
} from '@/pages/Workspace/hooks/groupChatRequestBuilder';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import { describe, expect, it } from '@jest/globals';

/**
 * 群聊桥路径请求构造器单测——根因 A（顶层 content）+ C（字段透传）的数据级验证。
 *
 * 不 mock useChatBridge：直接测 buildRequestParams 所调用的纯函数 buildGroupChatBridgeRequest，
 * 证明其产出顶层 content（SDK 默认 buildRequestParams 仅产 {userMessage:{content}} 无顶层 content → ws 帧 message 丢）。
 * 与 buildGroupChatPayload 组合可验到 ws 帧 payload.query 非空（见 groupChatProviderHelpers.test）。
 */
describe('buildGroupChatBridgeRequest — 根因 A 顶层 content + C 字段透传', () => {
  it('产顶层 content（非 SDK 默认仅 userMessage.content）', () => {
    const r = buildGroupChatBridgeRequest('s1', '杭州天气');
    expect(r.content).toBe('杭州天气'); // 顶层 content ← provider 读 params.content → query → ws 帧 message
    expect(r.userMessage.content).toBe('杭州天气');
    expect(r.sessionId).toBe('s1');
  });

  it('trim content', () => {
    expect(buildGroupChatBridgeRequest('s1', '  hi  ').content).toBe('hi');
  });

  it('透传 extra 的 botUuid/mentionAll/replyToMessageId/mentions（根因 C）', () => {
    const r = buildGroupChatBridgeRequest('s1', 'hi', {
      botUuid: 'bot-xyz',
      mentionAll: true,
      replyToMessageId: 'm1',
      mentions: ['bot-a'],
    });
    expect(r.botUuid).toBe('bot-xyz');
    expect(r.mentionAll).toBe(true);
    expect(r.replyToMessageId).toBe('m1');
    expect(r.mentions).toEqual(['bot-a']);
  });

  it('透传 panelContext/isInject（SDK buildBCSChatRequest 真读字段）', () => {
    const r = buildGroupChatBridgeRequest('s1', 'hi', { panelContext: { foo: 1 } as never, isInject: true });
    expect(r.panelContext).toEqual({ foo: 1 });
    expect(r.isInject).toBe(true);
  });

  it('isInject=false 显式透传（非 truthy 才挂的逻辑漏洞防护）', () => {
    const r = buildGroupChatBridgeRequest('s1', 'hi', { isInject: false });
    expect(r.isInject).toBe(false);
  });

  it('attachments 顶层透传 raw + userMessage.extra 带 echo（与直接路径 send 一致）', () => {
    const attachments: SessionMessageAttachment[] = [
      {
        attachment_id: 'f1',
        type: 'image',
        file_name: 'a.png',
        size: 10,
        url: 'https://share.example/f1',
      } as SessionMessageAttachment,
    ];
    const r = buildGroupChatBridgeRequest('s1', '看图', { attachments });
    expect(r.attachments).toBe(attachments); // 顶层 raw（SDK buildBCSChatRequest 用）
    expect(r.userMessage.extra.attachments?.[0].url).toMatch(/f1/); // echo 走会话内容地址
  });

  it('displayTime 始终在 userMessage.extra（本地回显一致）', () => {
    expect(buildGroupUserMessageExtra().displayTime).toEqual(expect.any(String));
    expect(buildEchoAttachments('s1', undefined)).toBeUndefined();
  });
});

// 真链路数据级组合验证（buildRequestParams → provider.request payload）：
// buildGroupChatBridgeRequest 顶层 content → buildGroupChatPayload 读 params.content → query。
// 完整在 test/services/workspace/groupChatProviderHelpers.test.ts 验 payload.query === content。
