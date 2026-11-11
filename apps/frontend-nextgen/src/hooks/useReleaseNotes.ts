// 版本发布说明 Hook（Open Core）。收口 capability + 红/Modal 状态编排。
// Open Core（capability null）→ supported:false，HelpMenu 不渲染「版本发布说明」项。
// Internal overlay 提供 load/markSeen → mount 单飞拉取，hasNew 比对 localStorage 决定红点。
// Hook ≤150 行（AGENTS.md）。纯读 capability，不直接 fetch，不发 yuyan 请求（capability 内部实现）。
import type { ReleaseNotesData } from '@/capabilities';
import { getCapabilities } from '@/capabilities';
import { useEffect, useState } from 'react';

export type ReleaseNotesStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface UseReleaseNotesResult {
  /** 当前运行形态是否支持版本发布说明（Open Core=false）。 */
  supported: boolean;
  status: ReleaseNotesStatus;
  data: ReleaseNotesData | null;
  /** 菜单「版本发布说明」项是否显示红点（有未读新版本）。 */
  hasNew: boolean;
  modalOpen: boolean;
  /** 打开 Modal；当前发布日期存在时立即标记已读并清红点。 */
  open: () => void;
  closeModal: () => void;
  /** 标记已读并关闭 Modal（写 localStorage 记录日期，清红点）。 */
  markSeenAndClose: () => void;
}

/**
 * 版本发布说明状态收口。mount 时若 supported 则单飞 load，比对 readSeenDate 判 hasNew。
 * 新发布日期首次加载时自动开 Modal 并标记已读；open() 手动打开时走相同已读逻辑。
 * markSeenAndClose() 保留关闭时兜底写入。
 */
export function useReleaseNotes(): UseReleaseNotesResult {
  const capResult = getCapabilities().getReleaseNotesCapability();
  const cap = capResult.value;
  const supported = !!cap;

  const [status, setStatus] = useState<ReleaseNotesStatus>(supported ? 'loading' : 'idle');
  const [data, setData] = useState<ReleaseNotesData | null>(null);
  const [hasNew, setHasNew] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    if (!cap) return;
    let cancelled = false;
    void cap.load().then((res) => {
      if (cancelled) return;
      if (res) {
        setData(res);
        setStatus('ready');
        // 新发布日期首次加载 → 自动展示一次；Modal 打开即标记已读并清红点。
        const seen = cap.getSeenDate();
        if (res.date && seen !== res.date) {
          cap.markSeen(res.date);
          setHasNew(false);
          setModalOpen(true);
        } else {
          setHasNew(false);
        }
      } else {
        setStatus('error');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [cap]);

  const open = () => {
    if (cap && data?.date) cap.markSeen(data.date);
    setHasNew(false);
    setModalOpen(true);
  };
  const closeModal = () => setModalOpen(false);
  const markSeenAndClose = () => {
    if (cap && data?.date) cap.markSeen(data.date);
    setHasNew(false);
    setModalOpen(false);
  };

  return { supported, status, data, hasNew, modalOpen, open, closeModal, markSeenAndClose };
}
