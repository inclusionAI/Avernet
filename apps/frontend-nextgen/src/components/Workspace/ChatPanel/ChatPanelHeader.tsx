import { IconButton } from '@/components/ui';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { ChevronRight, FolderOpen } from 'lucide-react';
import type { ReactNode } from 'react';

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
  /** 当前会话 ID（SVG 稿头部会话码胶囊，纯展示）。 */
  sessionCode?: string;
  connectionLabel?: string;
  connectionTone?: keyof typeof CONNECTION_TONE_TEXT;
  /** <lg 打开单聊会话列表。 */
  onOpenSessionList?: () => void;
  /** 打开会话文件面板（验收微调：文件管理入口迁至顶栏，与协作群位置规则一致；缺省不渲染入口）。 */
  onManageFiles?: () => void;
  /** 对话页头部图标列（chat-header-panels：五图标由页面装配，本组件只留插槽）。提供时取代文件入口的图标位。 */
  actions?: ReactNode;
}

/** 单聊顶栏（SVG 稿实测）：左 = 16px #09090B 标题 + 灰底会话码胶囊（满圆角、13px）；
 *  右 = 灰色功能图标列（当前仅「会话文件」为既有实功能，稿中其余图标位待功能落地后补齐，
 *  不做无行为的装饰图标）。稿中无头像与 Bot 摘要行——本组件只呈现，行为不变。
 *  标题保底 min-w 120px（2026-10-10 用户反馈）：窄屏下不被右侧元素挤压到立即省略，
 *  仅文本实际超出可用宽度时才 ellipsis。 */
export function ChatPanelHeader({
  target,
  sessionTitle,
  sessionCode,
  connectionLabel,
  connectionTone = 'neutral',
  onOpenSessionList,
  onManageFiles,
  actions,
}: ChatPanelHeaderProps) {
  return (
    <ChatLayout.Header
      className="flex h-12 items-center justify-between border-b border-border bg-card px-4 sm:px-5"
      slotRight={
        actions ??
        (onManageFiles ? (
          <IconButton
            label="会话文件"
            icon={<FolderOpen className="h-3.5 w-3.5" aria-hidden />}
            size="sm"
            className="text-muted-foreground"
            onClick={onManageFiles}
          />
        ) : null)
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
          <h2 className="m-0 min-w-[120px] truncate text-base font-medium text-foreground">
            {sessionTitle ?? target.name}
          </h2>
          {sessionCode ? (
            <span className="flex h-[30px] shrink-0 items-center rounded-full bg-selected px-3 text-[13px] text-content-strong">
              {sessionCode}
            </span>
          ) : null}
          {/* 连接状态为运行时数据；稿中为顶部横幅形态（P10），此处保留标题旁小字弱呈现，不新增交互。 */}
          {connectionLabel ? (
            <span className={`shrink-0 whitespace-nowrap text-xs ${CONNECTION_TONE_TEXT[connectionTone]}`}>
              {connectionLabel}
            </span>
          ) : null}
        </div>
      }
    />
  );
}
