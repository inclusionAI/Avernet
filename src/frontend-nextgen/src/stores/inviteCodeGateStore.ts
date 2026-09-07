import { create } from 'zustand';

/**
 * 邀请码门禁状态载体（纯状态，同步 setter，守分层）。与 `loginRedirectStore`（登录门禁）**独立**——
 * 两道门禁信号源各自单飞闭环，避免互相吞没（add-external-oauth-login 登录门禁 vs 本邀请码门禁）。
 *
 * `pendingPrompt` 为门禁弹窗信号（单飞：首信号胜出），由反应式（`inviteGateFailurePolicy` 命中
 * `invite_code_required` 403）或主动检测（`useInviteCodeGateBoot` 查 `/me` 得 `bound:false`）置位；
 * 全局 `InviteCodeBindingModal` 只读消费。提交成功后 `clearPrompt` + 整页 reload 回流。
 */
export interface InviteCodeBindingSnapshot {
  bound: boolean;
  bound_at?: number;
}

/** 弹窗内联错误态（提交失败 / 空输入）。`code` 为后端标准化错误码字符串，由 Hook 映射填入。 */
export interface InviteCodeSubmitError {
  code: string;
  message: string;
}

interface InviteCodeGateState {
  /** 最近一次已知绑定状态（提交成功后更新；初始 null=未知）。 */
  binding: InviteCodeBindingSnapshot | null;
  /** 提交绑定进行中（弹窗 CTA loading + 输入禁用）。 */
  submitting: boolean;
  /** 门禁弹窗是否应弹出（单飞信号）。 */
  pendingPrompt: boolean;
  /** 弹窗内联错误态。 */
  submitError: InviteCodeSubmitError | null;
  /** 登记门禁弹窗信号（单飞:已 pending 时 no-op，首信号胜出）。 */
  requestPrompt: () => void;
  /** 清除门禁弹窗信号（提交成功后调用，配合 reload 回流）。 */
  clearPrompt: () => void;
  setBinding: (binding: InviteCodeBindingSnapshot) => void;
  setSubmitting: (submitting: boolean) => void;
  setSubmitError: (error: InviteCodeSubmitError | null) => void;
  /** 重置（测试隔离用）。 */
  reset: () => void;
}

const initialState = {
  binding: null as InviteCodeBindingSnapshot | null,
  submitting: false,
  pendingPrompt: false,
  submitError: null as InviteCodeSubmitError | null,
};

export const useInviteCodeGateStore = create<InviteCodeGateState>((set, get) => ({
  ...initialState,
  requestPrompt: () => {
    if (get().pendingPrompt) return; // 单飞：已 pending 时 no-op（首信号胜出，避免并发命中重复弹）。
    set({ pendingPrompt: true });
  },
  clearPrompt: () => set({ pendingPrompt: false }),
  setBinding: (binding) => set({ binding }),
  setSubmitting: (submitting) => set({ submitting }),
  setSubmitError: (submitError) => set({ submitError }),
  reset: () => set(initialState),
}));
