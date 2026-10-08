import { IconButton } from '@/components/ui';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { ChevronRight, FolderOpen } from 'lucide-react';

/** 连接状态文字色：保留色调语义，去除胶囊底（轻文字与顶栏风格一致，与群聊 GroupHeader 统一）。 */
export const CONNECTION_TONE_TEXT: Record<'success' | 'warning' | 'error' | 'neutral', string> = {
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-destructive',
  neutral: 'text-muted-foreground',
};

export interface ChatPanelHeaderProps {
  target: ConversationTarget;
  sessionTitle?: string;
  connectionLabel?: string;
  connectionTone?: keyof typeof CONNECTION_TONE_TEXT;
  /** <lg 打开单聊会话列表。 */
  onOpenSessionList?: () => void;
  /** 打开会话文件面板（验收微调：文件管理入口迁至顶栏，与协作群位置规则一致；缺省不渲染入口）。 */
  onManageFiles?: () => void;
}

/** 单聊顶栏：头像 + 标题/连接状态 + 摘要（左）与文件入口（右）。自 ChatPanel 抽取，行为不变。 */
export function ChatPanelHeader({
  target,
  sessionTitle,
  connectionLabel,
  connectionTone = 'neutral',
  onOpenSessionList,
  onManageFiles,
}: ChatPanelHeaderProps) {
  return (
    <ChatLayout.Header
      className="flex h-16 items-center justify-between border-b border-border bg-card px-3 sm:px-5"
      slotRight={
        /* 验收微调：文件管理入口迁至顶栏右侧（与协作群 GroupHeader 的 FolderOpen 位置规则一致）。 */
        onManageFiles ? (
          <IconButton
            label="会话文件"
            icon={<FolderOpen className="h-4 w-4" aria-hidden />}
            size="sm"
            onClick={onManageFiles}
          />
        ) : null
      }
      slotLeft={
        <div className="flex min-w-0 items-center gap-3">
          {onOpenSessionList ? (
            <IconButton
              label="打开会话列表"
              icon={<ChevronRight className="h-5 w-5" aria-hidden />}
              size="sm"
              className="shrink-0 lg:hidden"
              onClick={onOpenSessionList}
            />
          ) : null}
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
            {target.avatar}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="m-0 truncate text-sm font-semibold">{sessionTitle ?? target.name}</h2>
              {/* v1.4：连接状态不收缩不换行——超长标题截断由 h2 独占，状态恒一行。 */}
              {connectionLabel ? (
                <span className={`shrink-0 whitespace-nowrap text-xs ${CONNECTION_TONE_TEXT[connectionTone]}`}>
                  {connectionLabel}
                </span>
              ) : null}
            </div>
            <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">{target.summary}</p>
          </div>
        </div>
      }
    />
  );
}
