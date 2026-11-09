// 版本发布说明 Hook（Open Core）。收口 capability + 红/Modal 状态编排。
// Open Core（capability null）→ supported:false，HelpMenu 不渲染「版本发布说明」项。
// Internal overlay 提供 load/markSeen → mount 单飞拉取，hasNew 比对 localStorage 决定红点。
// Hook ≤150 行（AGENTS.md）。纯读 capability，不直接 fetch，不发 yuyan 请求（capability 内部实现）。
import { getCapabilities } from '@/capabilities';
import { useEffect } from 'react';
import { useReleaseNotesStore, type ReleaseNotesStatus } from '@/stores/releaseNotesStore';

export type { ReleaseNotesStatus };

export interface UseReleaseNotesResult {
  /** 当前运行形态是否支持版本发布说明（Open Core=false）。 */
  supported: boolean;
  status: ReleaseNotesStatus;
  data: import('@/capabilities').ReleaseNotesData | null;
  /** 菜单「版本发布说明」项是否显示红点（有未读新发布且用户尚未主动关闭过弹窗）。 */
  hasNew: boolean;
  modalOpen: boolean;
  /** 打开 Modal；不写已读（用户主动关闭弹窗时统一收口已读）。 */
  open: () => void;
  /** 关闭 Modal 的统一收口（按钮/ESC/遮罩同路径）：markSeen 写已读 + 清红点。 */
  closeModal: () => void;
  /** 语义等价 closeModal（关闭即已读统一后保留的兼容出口）。 */
  markSeenAndClose: () => void;
}

/**
 * 版本发布说明状态收口（单实例）：状态与单飞拉取在 releaseNotesStore，多处消费共享
 * 同一份状态，消除重复 fetch 与「未渲染弹窗的实例静默 markSeen」缺陷。
 * 已读语义（2026-11-10 拍板）：用户主动关闭 Modal（按钮/ESC/遮罩统一走 closeModal）才
 * 视为已读；自动弹出/手动打开均不写——被动中断（刷新/路由）红点保留、下次可重弹。
 */
export function useReleaseNotes(): UseReleaseNotesResult {
  const cap = getCapabilities().getReleaseNotesCapability().value;
  const supported = !!cap;

  const status = useReleaseNotesStore((s) => s.status);
  const data = useReleaseNotesStore((s) => s.data);
  const hasNew = useReleaseNotesStore((s) => s.hasNew);
  const modalOpen = useReleaseNotesStore((s) => s.modalOpen);

  useEffect(() => {
    if (cap) useReleaseNotesStore.getState().initialize(cap);
  }, [cap]);

  const open = () => useReleaseNotesStore.getState().open();
  const closeModal = () => useReleaseNotesStore.getState().close();
  const markSeenAndClose = closeModal;

  return { supported, status, data, hasNew, modalOpen, open, closeModal, markSeenAndClose };
}
