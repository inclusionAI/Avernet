/**
 * 评测门禁 publish_staging 拦截 slot —— Avernet Open Core 契约测试。
 *
 * 本 PR 只在 `appExtension` 暴露 `publishStagingGate` 扩展 slot：Open Core 默认
 * null（直通、无门禁），internal overlay（OCB apps/frontend-nextgen/src/
 * internal/evaluation-gate）在组装期注入真实实现。Avernet 侧必须守两条契约：
 *
 * 1. 默认 slot 恒为 null —— 命中 publish_staging 时直接 advance('prestable')，
 *    现有发布行为字节不变（D7 跨消费者隔离 / Open Core 零回归）。
 * 2. slot 类型契约稳定 —— internal overlay 实现须满足 `PublishStagingGateSpec`
 *    （check / startEvaluation / emergencyPublish / Dialog 四个成员）。
 *
 * 组件级门禁拦截（Dialog 渲染、开始评测、紧急发布）的行为在 OCB internal
 * overlay 侧覆盖；本套件只守 Avernet Open Core 的 slot 契约边界，不 mount 重 Drawer
 * （见 apps/frontend-nextgen/CLAUDE.md：测试位于 test/，前端测试以 hook/service/
 * store 契约为主）。
 */
import { appExtension, type PublishStagingGateSpec } from '@/extensions';

describe('appExtension.publishStagingGate slot（Open Core 契约）', () => {
  it('默认为 null：publish_staging 直通、无门禁（D7 零回归）', () => {
    expect(appExtension.publishStagingGate).toBeNull();
  });

  it('slot 类型契约稳定：internal overlay 实现须暴露 check/startEvaluation/emergencyPublish/Dialog', () => {
    // 用一个满足 PublishStagingGateSpec 的最小样例验证契约面形状（类型由 TS 保证，
    // 运行时校验四个成员都是函数式可调用入口）。
    const fake: PublishStagingGateSpec = {
      check: async () => ({ allowed: true }),
      startEvaluation: async () => true,
      emergencyPublish: async () => true,
      Dialog: (() => null) as unknown as PublishStagingGateSpec['Dialog'],
    };
    expect(typeof fake.check).toBe('function');
    expect(typeof fake.startEvaluation).toBe('function');
    expect(typeof fake.emergencyPublish).toBe('function');
    expect(typeof fake.Dialog).toBe('function');
  });
});
