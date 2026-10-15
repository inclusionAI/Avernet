/**
 * GroupHistorySidebar —— 协作群历史消息右缘面板容器（入口=头部「历史消息」图标）。
 *
 * 复用 chat 页的 SessionHistorySearchPanel 作面板本体（组件无 chat 会话耦合，仅
 * search 模型 + onLocate 两个 prop）；容器与 ChatHeaderRightPanel 同构
 * （ResizableWorkspaceSidebar 320-480px 可拖宽，默认 320，48px 头行：标题 + 关闭）。
 * 宽度持久化 key 独立于 chat 侧，避免两页共抢同一 localStorage 槽。
 */
import { IconButton } from '@/components/ui';
import { SessionHistorySearchPanel } from '@/pages/Workspace/Chat/components/SessionHistorySearchPanel';
import type { UseSessionHistorySearchModel } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import { X } from 'lucide-react';
import { ResizableWorkspaceSidebar } from '../ResizableWorkspaceSidebar';

export interface GroupHistorySidebarProps {
  search: UseSessionHistorySearchModel;
  /** 点击搜索结果：上层定位滚动 + 高亮（locateMessage）。 */
  onLocate: (messageId: string) => void;
  onClose: () => void;
}

export function GroupHistorySidebar({ search, onLocate, onClose }: GroupHistorySidebarProps) {
  return (
    <ResizableWorkspaceSidebar
      ariaLabel="历史消息面板"
      side="right"
      onDismiss={onClose}
      minWidth={320}
      maxWidth={480}
      defaultWidth={320}
      storageKey="teamclaw:group-history-panel-width"
      className="z-30 border-l border-border bg-card"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
          <h3 className="m-0 text-sm font-medium text-foreground">历史消息</h3>
          <IconButton label="关闭面板" icon={<X className="size-4" />} variant="ghost" size="sm" onClick={onClose} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
          <SessionHistorySearchPanel search={search} onLocate={onLocate} />
        </div>
      </div>
    </ResizableWorkspaceSidebar>
  );
}

export default GroupHistorySidebar;
