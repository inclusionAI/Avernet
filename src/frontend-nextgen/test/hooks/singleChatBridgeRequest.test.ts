import { buildSingleChatBridgeRequest } from '@/hooks/singleChatBridgeRequest';
import { describe, expect, it } from '@jest/globals';

/**
 * 单聊桥路径请求构造器单测——根因 A（顶层 content）single-chat 侧数据级验证。
 *
 * useWorkspace 的 useChatBridge.buildRequestParams 调此函数。证明产出顶层 content
 * （SDK 默认仅 {userMessage:{content}} → botChatProvider:174/supportProvider:152 读 params.content → query 丢）。
 * 统一形态含 targetId:support provider 验证用（supportProvider.request:153），bot provider 不读无害。
 */
describe('buildSingleChatBridgeRequest — 根因 A 顶层 content + 统一形态', () => {
  it('产顶层 content + targetId + userMessage（统一形态,满足 SupportChatRequest）', () => {
    const r = buildSingleChatBridgeRequest('target-1', '杭州天气');
    expect(r.content).toBe('杭州天气');
    expect(r.targetId).toBe('target-1');
    expect(r.userMessage.content).toBe('杭州天气');
    expect(r.userMessage.extra.displayTime).toEqual(expect.any(String));
  });

  it('activeTargetId 为 null 时 targetId 兜底空串（bot 路径 provider 不读,无害；support 路径不会到此分支）', () => {
    const r = buildSingleChatBridgeRequest(null, 'hi');
    expect(r.content).toBe('hi');
    expect(r.targetId).toBe('');
  });
});
