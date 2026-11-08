/**
 * GroupHistorySidebar —— 协作群历史消息右缘面板容器（入口=头部「历史消息」图标）。
 *
 * 复用 chat 页的 SessionHistorySearchPanel 作面板本体（组件无 chat 会话耦合，仅
 * search 模型 + onLocate 两个 prop）；容器与 ChatHeaderRightPanel 同构
 * （WorkspaceOverlayPanel，48px 头行：标题 + 关闭）。
 * 默认宽度、拖宽范围和宽度记忆与其余聊天右侧面板共用。
 */
import { IconButton } from '@/components/ui';
import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import { SessionHistorySearchPanel } from '@/pages/Workspace/Chat/components/SessionHistorySearchPanel';
import type { UseSessionHistorySearchModel } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import { X } from 'lucide-react';
import { WorkspaceOverlayPanel } from '../WorkspaceOverlayPanel';

export interface GroupHistorySidebarProps {
  search: UseSessionHistorySearchModel;
  /** 点击搜索结果：上层定位滚动 + 高亮（locateMessage）。 */
  onLocate: (messageId: string) => void;
  onClose: () => void;
}

export function GroupHistorySidebar({ search, onLocate, onClose }: GroupHistorySidebarProps) {
  return (
    <WorkspaceOverlayPanel
      ariaLabel="历史消息面板"
      onDismiss={onClose}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="z-30 border-l border-border bg-card"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
          <h3 className="m-0 text-sm font-medium text-foreground">历史消息</h3>
          <IconButton label="关闭面板" icon={<X className="size-4" />} variant="ghost" size="sm" onClick={onClose} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
          <SessionHistorySearchPanel
            search={search}
            onLocate={(messageId) => {
              onLocate(messageId);
              onClose();
            }}
          />
        </div>
      </div>
    </WorkspaceOverlayPanel>
  );
}

export default GroupHistorySidebar;
