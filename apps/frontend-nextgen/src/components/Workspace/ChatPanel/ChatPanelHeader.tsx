import { IconButton } from '@/components/ui';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { ChevronRight, Copy, FolderOpen } from 'lucide-react';
import type { ReactNode } from 'react';
import { toast } from 'sonner';

/**
 * 连接状态点色：语义收敛到 6px 状态点，文字统一弱灰（2026-10-14 用户反馈：整段彩色
 * 文字过于突兀，与 GroupHeader 顶栏弱呈现口径一致）。
 */
export const CONNECTION_TONE_DOT: Record<'success' | 'warning' | 'error' | 'neutral', string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-destructive',
  neutral: 'bg-muted-foreground/40',
};

export interface ChatPanelHeaderProps {
  target: ConversationTarget;
  sessionTitle?: string;
  /** 当前会话 ID 胶囊：点按整颗复制（含 Copy 图标在右侧自揭示）。 */
  sessionCode?: string;
  connectionLabel?: string;
  connectionTone?: keyof typeof CONNECTION_TONE_DOT;
  /** <lg 打开单聊会话列表。 */
  onOpenSessionList?: () => void;
  /** 打开会话文件面板（验收微调：文件管理入口迁至顶栏，与协作群位置规则一致；缺省不渲染入口）。 */
  onManageFiles?: () => void;
  /** 对话页头部图标列（chat-header-panels：五图标由页面装配，本组件只留插槽）。提供时取代文件入口的图标位。 */
  actions?: ReactNode;
}

/**
 * 单聊顶栏（SVG 稿实测）：左 = 16px #09090B 标题 + 会话 ID 胶囊（可点按复制）+ 连接状态点；
 * 右 = 灰色功能图标列（chat-header-panels 五图标由页面装配）。
 *
 * 窄屏让位次序（2026-10-14 用户反馈：宽度不足时左侧与右侧五图标重叠）——ChatLayout 槽为
 * 左右各 flex:1 五五分，左侧内容必须可收缩：① 会话 ID 胶囊双倍收缩（shrink-2 + 内部
 * truncate，极限退化到仅 Copy 图标）；② 标题保底 112px（sm 以上 120px，2026-10-10 反馈）
 * 后才 ellipsis；③ 状态点与返回钮 shrink-0 不参与收缩。胶囊宽上限 180px 对齐 GroupHeader。
 */
export function ChatPanelHeader({ target, sessionTitle, sessionCode, connectionLabel, connectionTone = 'neutral', onOpenSessionList, onManageFiles, actions }: ChatPanelHeaderProps) {
  const copySessionId = async () => {
    if (!sessionCode) return;
    try {
      await navigator.clipboard.writeText(sessionCode);
      toast.success('会话 ID 已复制');
    } catch {
      toast.error('复制失败，请手动复制。');
    }
  };

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
        <div className="flex min-w-0 flex-1 items-center gap-2.5 sm:gap-3">
          {onOpenSessionList ? (
            <IconButton
              label="打开会话列表"
              icon={<ChevronRight className="h-5 w-5" aria-hidden />}
              size="sm"
              className="shrink-0 lg:hidden"
              onClick={onOpenSessionList}
            />
          ) : null}
          <h2 className="m-0 min-w-[112px] shrink truncate text-base font-medium text-foreground sm:min-w-[120px]">
            {sessionTitle ?? target.name}
          </h2>
          {sessionCode ? (
            <button
              type="button"
              onClick={() => void copySessionId()}
              aria-label={`复制会话 ID ${sessionCode}`}
              title={sessionCode}
              className="flex h-6 min-w-0 max-w-[180px] shrink-[2] items-center gap-1 rounded-full bg-secondary px-2 text-[11px] text-foreground/75 transition-colors hover:bg-foreground/10 hover:text-foreground"
            >
              <span className="min-w-0 truncate">{sessionCode}</span>
              <Copy className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
            </button>
          ) : null}
          {/* 连接状态为运行时数据；弱呈现 = 色点（tone 语义）+ 弱灰文字，不与标题抢视觉。 */}
          {connectionLabel ? (
            <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
              <span aria-hidden className={`size-1.5 shrink-0 rounded-full ${CONNECTION_TONE_DOT[connectionTone]}`} />
              {connectionLabel}
            </span>
          ) : null}
        </div>
      }
    />
  );
}
