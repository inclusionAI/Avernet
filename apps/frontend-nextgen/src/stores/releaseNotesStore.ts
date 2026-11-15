// 版本发布说明全局状态（单实例收口）。此前 useReleaseNotes 各消费点各持一份本地 state
// 并重复 load（HelpMenu + 内源 GuideAssistantPanel 双实例竞争，存在「未渲染弹窗的实例
// 静默 markSeen」缺陷，2026-11-10 修复）；状态与单飞拉取上提至本 store，多消费点共享。
//
// 已读语义（2026-11-10 拍板）：用户「主动关闭」Modal（按钮 / ESC / 点遮罩，统一走
// close 收口）= 已读，此时才 markSeen 并清红点；自动弹出与手动打开均不写已读——
// 弹窗开着时被动中断（刷新/路由/卸载）不写，红点保留、下次进入可重弹。
import type { ReleaseNotesCapability, ReleaseNotesData } from '@/capabilities';
import { create } from 'zustand';

export type ReleaseNotesStatus = 'idle' | 'loading' | 'ready' | 'error';

/** 当前生效 capability（initialize 注入；capability 对象运行期稳定）。 */
let activeCap: ReleaseNotesCapability | null = null;

interface ReleaseNotesState {
  status: ReleaseNotesStatus;
  data: ReleaseNotesData | null;
  /** 菜单「版本发布说明」项红点（有未读新发布且用户尚未主动关闭过弹窗）。 */
  hasNew: boolean;
  modalOpen: boolean;
  /** 本次未读发布标识（load 结果 stamp ?? date）；close 时经 capability.markSeen 写入。 */
  stamp: string | null;
  /** 单飞初始化：capability null（Open Core）时由调用方跳过；loading/ready 不重复拉取。 */
  initialize: (cap: ReleaseNotesCapability) => void;
  /** 手动打开（不清红点、不写已读——关闭时统一收口）。 */
  open: () => void;
  /** 统一关闭收口：任何用户主动关闭路径（按钮/ESC/遮罩）一律 markSeen + 清红点。 */
  close: () => void;
  /** 测试隔离：恢复初始态并清空 activeCap。 */
  reset: () => void;
}

export const useReleaseNotesStore = create<ReleaseNotesState>((set, get) => ({
  status: 'idle',
  data: null,
  hasNew: false,
  modalOpen: false,
  stamp: null,
  initialize: (cap) => {
    activeCap = cap;
    const { status } = get();
    if (status === 'loading' || status === 'ready') return; // 单飞：已发起/完成不重复拉取
    set({ status: 'loading' });
    void cap
      .load()
      .then((res) => {
        if (activeCap !== cap) return; // 防御：测试换代后的晚到结果
        if (res) {
          // 发布标识优先 stamp（内源组合标识，含内容指纹），未提供时回退 date；
          // 均缺失（配置单未填标识）→ 不触发红点与自动弹出（T5 降级）。
          const stamp = res.stamp ?? res.date ?? null;
          const seen = cap.getSeenDate();
          const hasNew = stamp !== null && seen !== stamp;
          set({ data: res, status: 'ready', stamp, hasNew, modalOpen: hasNew });
        } else {
          set({ status: 'error' });
        }
      })
      .catch(() => {
        if (activeCap === cap) set({ status: 'error' });
      });
  },
  open: () => set({ modalOpen: true }),
  close: () => {
    const { stamp } = get();
    if (activeCap && stamp) activeCap.markSeen(stamp);
    set({ hasNew: false, modalOpen: false });
  },
  reset: () => {
    activeCap = null;
    set({ status: 'idle', data: null, hasNew: false, modalOpen: false, stamp: null });
  },
}));
