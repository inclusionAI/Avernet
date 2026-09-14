import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';

/**
 * 监听 wsReconnectNonce：消息视角切换成功后 store 递增该值，
 * 这里对当前 provider 触发整体重连（reconnect 内部重拉一次性 token）；
 * 重连成功后调用 onReconnected（如以不变参数刷新历史消息），失败仅 toast 不回调。
 *
 * 注意回调重叠：视角切换按钮路径会同时触发本 Hook 的 onReconnected 与
 * 服务端 view_scope_changed 推送路径（useViewScopeChangedNotice）的回调，
 * 历史刷新可能执行两次——REST 拉取幂等、setMessages 后者覆盖前者，可接受。
 *
 * 从 useGroupChat 拆出以守住 Hook ≤250 行门禁（useGroupChat 已 243 行）；
 * mount 首跳忽略（以 ref 记录最近一次已消费的 nonce，仅响应后续变化）。
 */
export function useWsReconnectNonce(provider: GroupChatProvider | null, onReconnected?: () => void): void {
  const wsReconnectNonce = useWorkspaceStore((s) => s.wsReconnectNonce);
  const lastNonceRef = useRef<number | null>(null);

  useEffect(() => {
    if (lastNonceRef.current === null) {
      lastNonceRef.current = wsReconnectNonce;
      return;
    }
    if (wsReconnectNonce === lastNonceRef.current) return;
    lastNonceRef.current = wsReconnectNonce;
    if (!provider) return;
    void provider
      .reconnect()
      .then(() => onReconnected?.())
      .catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : '重连失败');
      });
  }, [wsReconnectNonce, provider, onReconnected]);
}
