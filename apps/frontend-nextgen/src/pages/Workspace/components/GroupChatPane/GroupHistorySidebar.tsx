/**
 * GroupHistorySidebar —— 协作群历史消息右缘面板容器（入口=头部「历史消息」图标）。
 *
 * 复用 chat 页的 SessionHistorySearchPanel 作面板本体（组件无 chat 会话耦合，仅
 * search 模型 + onLocate 两个 prop）；容器复用 ChatHeaderRightPanel——对话区内联
 * 副屏呈现（对话区随面板让宽、无蒙层抽屉，与 chat 页历史消息同形）。
 * 默认宽度、拖宽范围和宽度记忆与其余聊天右侧面板共用。
 */
import { ChatHeaderRightPanel } from '@/pages/Workspace/components/WorkspaceHeaderRightPanel';
import { SessionHistorySearchPanel } from '@/pages/Workspace/Chat/components/SessionHistorySearchPanel';
import type { UseSessionHistorySearchModel } from '@/services/workspace/conversationSearchService';

export interface GroupHistorySidebarProps {
  /** 展开态；false → 宽度收 0 + 内容退出可交互，容器保持挂载形成展开/收起过渡。 */
  open?: boolean;
  search: UseSessionHistorySearchModel;
  /** 点击搜索结果：上层定位滚动 + 高亮（locateMessage）。 */
  onLocate: (messageId: string) => void;
  onClose: () => void;
}

export function GroupHistorySidebar({ open = true, search, onLocate, onClose }: GroupHistorySidebarProps) {
  return (
    <ChatHeaderRightPanel title="历史消息" open={open} onClose={onClose}>
      <SessionHistorySearchPanel
        search={search}
        onLocate={(messageId) => {
          onLocate(messageId);
          onClose();
        }}
      />
    </ChatHeaderRightPanel>
  );
}

export default GroupHistorySidebar;
