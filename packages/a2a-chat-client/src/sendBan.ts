/**
 * B12 — context 级发送禁令注册表（spec stage2 §3.1）。
 * ============================================================================
 *
 * spec 规则 → 方法核销表（每条规则一行，本类是唯一事实源）：
 *   · §3.1 禁令是 CONTEXT 级状态，键 = (endpointBase, contextId)——独立于
 *     provider 实例生命周期：注册表只认键，不持任何 provider 引用；provider
 *     换代/reconnect/重建对它不可见。ARP 命名恶意不清洗——释放只有下面
 *     两条正路。begin()
 *   · §3.1 清扫监听/断连善后 ≠ 清禁令：本类没有任何"生命周期/清理"方法
 *     （结构上杜绝误用——想清禁令只有 releaseOnConfirmedAbort /
 *     releaseOnTerminal 两个语义位）。
 *   · §3.1 CANCELED：CancelTask 派发即乐观挂禁（provider 侧 begin），
 *     等待控制面结果；**仅 engineAbort=confirmed 自动解禁**。
 *     releaseOnConfirmedAbort()
 *   · §3.1 非 cancel 终态（COMPLETED/FAILED 等）按正常完成语义解禁；
 *     GetTask 确认到的终态进同一解禁流。releaseOnTerminal()
 *   · §3.1 用户放弃恢复 / GetTask not-found / 切走：不证明底层已停——
 *     禁令保持（规则=没有对应释放方法，防误清）。
 *   · §3.1 重复 CancelTask 不是 workaround（引擎 task_terminal repeat cancel
 *     不再发 abort）——B13 消费 isBanned() 挡新 send 并提示"上下文暂时
 *     无法继续"，不建议重复取消。
 *
 * 命名注：类名 SendBanRegistry（registry 是接入面；"清禁令"没有公共通道）。
 * 不导出单例：B13 组合时用 createSendBanRegistry() 自持（跨 provider 共享
 * 同一实例即得 context 级生存期）；A2AProvider 缺省用模块级私有缺省实例。
 */
export class SendBanRegistry {
  /** 键 → 挂禁记录（值成员即键镜像，调试/断言可读；无派生状态） */
  private readonly banned = new Map<string, { endpointBase: string; contextId: string }>();

  private static keyOf(endpointBase: string, contextId: string): string {
    return `${endpointBase}\x00${contextId}`;
  }

  /** 挂禁（幂等：重复 begin 保持原状，不重置任何计时） */
  begin(endpointBase: string, contextId: string): void {
    const key = SendBanRegistry.keyOf(endpointBase, contextId);
    if (!this.banned.has(key)) {
      this.banned.set(key, { endpointBase, contextId });
    }
  }

  isBanned(endpointBase: string, contextId: string): boolean {
    return this.banned.has(SendBanRegistry.keyOf(endpointBase, contextId));
  }

  /**
   * §3.1 查询口径（context 级存在性）：该 context 是否挂有**任意** endpoint 的
   * 未终结禁令。挂禁/核销仍按 (endpointBase, contextId) 精确键；**查询**必须
   * context 级——灰度回滚移除 A2A 地址后，当前连接推导出的 endpoint 可能与
   * 挂禁时的面不同源（chat 中继 vs 独立 A2A 面），按当前面推导键查询会
   * 把旧任务的禁令查丢、误放行（"查不到 ≠ 没禁令"）。
   */
  isContextBanned(contextId: string): boolean {
    for (const rec of this.banned.values()) {
      if (rec.contextId === contextId) return true;
    }
    return false;
  }

  /**
   * §3.1 唯一自动解禁位：CancelTask 控制面 engineAbort=confirmed 落地
   * （cancelled-clean 终局的核销）。非 confirmed 结局绝不走此方法
   * （unconfirmed/unknown 保持挂禁——"解禁只认 confirmed"红线）。
   */
  releaseOnConfirmedAbort(endpointBase: string, contextId: string): void {
    this.banned.delete(SendBanRegistry.keyOf(endpointBase, contextId));
  }

  /**
   * 非 cancel 终态的解禁流（COMPLETED/FAILED 等按正常完成语义；GetTask
   * 确认到的终态同路）。provider 端只对 CANCELED 之外的终态调用——
   * CANCELED 永远只走 releaseOnConfirmedAbort。
   */
  releaseOnTerminal(endpointBase: string, contextId: string): void {
    this.banned.delete(SendBanRegistry.keyOf(endpointBase, contextId));
  }
}

/** 工厂（非单例导出）：B13 组合入口 + 测试自持隔离。 */
export function createSendBanRegistry(): SendBanRegistry {
  return new SendBanRegistry();
}