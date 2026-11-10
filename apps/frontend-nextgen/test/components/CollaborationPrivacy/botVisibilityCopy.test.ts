// Bot 好友审批入口深链（split-admin-space-ticket-pages）：approvalEntryPath 改指独立通知中心路由，
// 不再经 /admin?tab=work-orders 单页；入口文案随页面拆分同步为「通知中心」。
import { botFriendApprovalSection } from '@/components/CollaborationPrivacy/botVisibilityCopy';
import { describe, expect, it } from '@jest/globals';

describe('botFriendApprovalSection 审批入口深链', () => {
  it('approvalEntryPath 指向 /ticket-center', () => {
    expect(botFriendApprovalSection.approvalEntryPath).toBe('/ticket-center');
    expect(botFriendApprovalSection.approvalEntryPath).not.toContain('admin');
  });

  it('入口文案不再提及管理后台', () => {
    expect(botFriendApprovalSection.approvalEntryLabel).toContain('通知中心');
    expect(botFriendApprovalSection.approvalEntryLabel).not.toContain('管理后台');
  });
});
