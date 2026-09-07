import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';

/**
 * 订阅邀请码门禁弹窗信号（只读 `{ open }`），供 `InviteCodeBindingModal` 消费（组件不直接 import store，守分层）。
 * `pendingPrompt` 由反应式（`inviteGateFailurePolicy` 命中 `invite_code_required` 403）或主动检测
 * （`useInviteCodeGateBoot` 查 `/me` 得 `bound:false`）单飞置位。弹窗不可关闭，唯一出路是提交有效邀请码。
 */
export function useInviteCodePrompt(): { open: boolean } {
  const open = useInviteCodeGateStore((s) => s.pendingPrompt);
  return { open };
}
