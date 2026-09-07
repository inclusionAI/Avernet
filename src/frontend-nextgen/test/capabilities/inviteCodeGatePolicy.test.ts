import { defaultCapabilities } from '@/capabilities/defaultCapabilities';

/**
 * 邀请码门禁策略 capability（add-invite-code-gate）：
 * Open Core 默认 `enabled`（=阿里云外部生产形态，gate 为产品对外准入控制）。
 * internal overlay 覆盖为 `disabled`（员工形态，ACE 后端无邀请码端点）——见 src/extensions/internal.ts。
 */
describe('getInviteCodeGatePolicy capability', () => {
  it('Open Core 默认 enabled（=阿里云外部形态）', () => {
    const r = defaultCapabilities.getInviteCodeGatePolicy();
    expect(r.status).toBe('available');
    expect(r.value).toBe('enabled');
  });

  it('同步签名（返回值稳定，不发请求）', () => {
    expect(defaultCapabilities.getInviteCodeGatePolicy()).toEqual(defaultCapabilities.getInviteCodeGatePolicy());
  });
});
