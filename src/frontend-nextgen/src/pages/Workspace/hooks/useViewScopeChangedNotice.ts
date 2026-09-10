import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useEffect } from 'react';
import { toast } from 'sonner';

/**
 * 服务端在线修改 message_view_scope 后会以 view_scope_changed 关闭 ws：
 * provider 内部自动整体重连，本 Hook 负责用户可见提示，并同步调用
 * onViewScopeChanged（如以不变参数刷新历史消息，与切换按钮路径保持一致）。
 */
export function useViewScopeChangedNotice(provider: GroupChatProvider | null, onViewScopeChanged?: () => void): void {
  useEffect(() => {
    if (!provider) return;
    return provider.subscribeToViewScopeChanged(() => {
      toast.info('消息视角已更新，正在重连');
      onViewScopeChanged?.();
    });
  }, [provider, onViewScopeChanged]);
}
