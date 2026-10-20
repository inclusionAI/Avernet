/**
 * B12 — SendBanRegistry §3.1 规则行单元测试（纯内存）。
 * 规则 → 方法核销表见 src/adapters/a2a/sendBan.ts 头注；本测试逐行钉：
 *   · 键隔离：(endpointBase, contextId) 二元组独立；
 *   · 粘性：只有 releaseOnConfirmedAbort / releaseOnTerminal 两条正路，
 *     其余任何操作不清禁令（"放弃恢复/not-found/切走"无释放通道——结构钉）；
 *   · 释放后可重挂（下一轮取消的乐观位照开）；
 *   · context 级生命周期：与 provider 实例无关（registry 不持 provider 引用，
 *     两处代码共享同一实例即同键同状态——provider 级断言见 provider.test.ts B12 块）。
 */
import { createSendBanRegistry } from '@/adapters/a2a/sendBan';

describe('SendBanRegistry — §3.1 规则行核销', () => {
  const EP = 'https://eng.example/rpc';
  const OTHER_EP = 'https://other.example/rpc';

  it('begin → isBanned 幂等挂禁（重复 begin 不产生副作用）', () => {
    const reg = createSendBanRegistry();
    expect(reg.isBanned(EP, 'ctx-1')).toBe(false);
    reg.begin(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(true);
    reg.begin(EP, 'ctx-1');
    reg.begin(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(true);
  });

  it('键隔离：context 维与 endpoint 维各自独立', () => {
    const reg = createSendBanRegistry();
    reg.begin(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-2')).toBe(false);      // 同端点不同 context
    expect(reg.isBanned(OTHER_EP, 'ctx-1')).toBe(false); // 同 context 不同端点
    reg.begin(OTHER_EP, 'ctx-1');
    expect(reg.isBanned(OTHER_EP, 'ctx-1')).toBe(true);
  });

  it('releaseOnConfirmedAbort：唯自动解禁位（engineAbort=confirmed 落地侧）', () => {
    const reg = createSendBanRegistry();
    reg.begin(EP, 'ctx-1');
    reg.releaseOnConfirmedAbort(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(false);
    // 释放幂等：非挂禁状态重复释放无副作用
    reg.releaseOnConfirmedAbort(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(false);
  });

  it('releaseOnTerminal：非 cancel 终态/GetTask 确认终态解禁；与 confirmed 位同效', () => {
    const reg = createSendBanRegistry();
    reg.begin(EP, 'ctx-1');
    reg.releaseOnTerminal(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(false);
  });

  it('粘性（§3.1）：用户放弃恢复 / GetTask not-found / 切走 provider 无释放通道——'
    + '两次 begin+零释放之间挂禁永续；释放后重开新禁照常可挂', () => {
    const reg = createSendBanRegistry();
    reg.begin(EP, 'ctx-1');
    // 中途任意次 begin（新取消轮乐观位）与 isBanned 轮询均不解禁
    reg.begin(EP, 'ctx-1');
    for (let i = 0; i < 3; i += 1) {
      expect(reg.isBanned(EP, 'ctx-1')).toBe(true);
    }
    // 正路释放后：下一轮取消可重新挂禁（同年同键重挂生效）
    reg.releaseOnTerminal(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(false);
    reg.begin(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(true);
  });

  it('context 级生命周期：共享实例跨"provider 换代"同键同状态', () => {
    const reg = createSendBanRegistry();
    reg.begin(EP, 'ctx-1');
    // B13 组合形态：新的消费方只需持有同一 registry 引用（provider 换代不建新表）
    const consumerAlias = reg;
    expect(consumerAlias.isBanned(EP, 'ctx-1')).toBe(true);
    consumerAlias.releaseOnConfirmedAbort(EP, 'ctx-1');
    expect(reg.isBanned(EP, 'ctx-1')).toBe(false);   // 同实例同状态（引用共享）
    // 独立实例互不可见：context 级生存期由组合方持单实例实现（非单例导出）
    const isolated = createSendBanRegistry();
    expect(isolated.isBanned(EP, 'ctx-1')).toBe(false);
  });
});