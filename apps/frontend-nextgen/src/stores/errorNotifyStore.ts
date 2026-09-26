import { create } from 'zustand';

/**
 * 接口失败「默认提示」的待投递队列(纯状态,守分层:只同步 setter,不调 Service/组件/toast)。
 *
 * 来源:两条请求通道(通道 B `backendRequest`、通道 A `umi requestConfig`)在 service/协议边界检测到失败时,
 * 只调 `enqueue`「敲门」——把待提示记录入队并 throw。toast 由顶层观察者 `useErrorNotifyObserver`(Hook 层)
 * 订阅本 store、`drain` 后统一发起。这样协议层不必依赖 sonner/DOM(守 `src/services` 禁 toast),仍能保证
 * 「接口失败默认提示用户」。同构于 `loginRedirectStore` + `useGatewayLoginRedirect` 范式。
 *
 * 去重:本 store 不做时间窗去重(那在 `notifyError` 的冷静窗内完成);`cancel(toastKey)` 标记取消,
 * 供 Hook 在观察者兜底前(短延迟内)显式取消默认提示(自定义接管或静默)。
 */
export interface ErrorNotifyItem {
  toastKey: string;
  message: string;
  apiPath?: string;
  operation?: string;
  /** 入队时间戳(ms);便于排查与未来风控,当前未参与去重判定。 */
  ts: number;
  /** 被 `cancel` 标记的项观察者 `drain` 后跳过,不发起 toast。 */
  cancelled?: boolean;
}

interface ErrorNotifyStoreState {
  queue: ErrorNotifyItem[];
  /** 入队一条待提示记录(时间戳由 store 注入)。 */
  enqueue: (item: Omit<ErrorNotifyItem, 'ts'>) => void;
  /** 标记给定 toastKey 的待提示记录为取消(观察者 drain 后跳过)。 */
  cancel: (toastKey: string) => void;
  /** 拉取当前队列并清空;返回项含 cancelled 标志,由观察者按需跳过。 */
  drain: () => ErrorNotifyItem[];
  /** 重置(测试隔离用)。 */
  reset: () => void;
}

export const useErrorNotifyStore = create<ErrorNotifyStoreState>((set, get) => ({
  queue: [],
  enqueue: (item) => set((state) => ({ queue: [...state.queue, { ...item, ts: Date.now() }] })),
  cancel: (toastKey) =>
    set((state) => ({
      queue: state.queue.map((it) => (it.toastKey === toastKey ? { ...it, cancelled: true } : it)),
    })),
  drain: () => {
    const drained = get().queue;
    set({ queue: [] });
    return drained;
  },
  reset: () => set({ queue: [] }),
}));
