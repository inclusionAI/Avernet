import { Button, IconButton } from '@/components/ui';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { ChevronRight, Copy, FolderOpen } from 'lucide-react';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { PrimaryNavToggle } from '../PrimaryNav';
import { ChatConnectionStatus, CONNECTION_TONE_DOT } from '../ChatConnectionStatus';

export { CONNECTION_TONE_DOT } from '../ChatConnectionStatus';

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
 * 单聊顶栏：与群聊同形的描边身份容器，内含标题与会话 ID 胶囊；连接状态独立在旁。
 * 右 = 灰色功能图标列（chat-header-panels 五图标由页面装配）。
 *
 * 窄屏让位次序（2026-10-14 用户反馈：宽度不足时左侧与右侧五图标重叠）——ChatLayout 槽为
 * 左右各 flex:1 五五分，左侧内容必须可收缩：① 会话 ID 胶囊双倍收缩（shrink-2 + 内部
 * truncate，极限退化到仅 Copy 图标）；② 标题可截断；③ 状态点与返回钮不参与收缩。
 * 胶囊宽上限 180px 对齐 GroupHeader。④（2026-10-19）「已连接」文案 <lg 收纳为仅绿点，
 * 可访问名由 ChatConnectionStatus 的 aria-label 恒撑（读屏/悬停不丢失）。
 */
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
      className="flex h-12 items-center justify-between border-b border-divider bg-card px-3 sm:px-4"
      slotRight={
        actions ??
        (onManageFiles ? (
          <IconButton
            label="会话文件"
            icon={<FolderOpen className="h-3.5 w-3.5" aria-hidden />}
            size="sm"
            data-workspace-panel-trigger
            className="text-muted-foreground"
            onClick={onManageFiles}
          />
        ) : null)
      }
      slotLeft={
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {/* 壳内一级导航入口（<lg）：置于「打开会话列表」左侧同排，取代内容区顶部独立条带；
              壳外/精简路由上下文为 null 时本钮不渲染，组件仍可独立渲染（单测直渲形态）。 */}
          <PrimaryNavToggle />
          {onOpenSessionList ? (
            <IconButton
              label="打开会话列表"
              icon={<ChevronRight className="h-5 w-5" aria-hidden />}
              size="sm"
              className="shrink-0 lg:hidden"
              onClick={onOpenSessionList}
            />
          ) : null}
          <div
            role="group"
            aria-label={`对话 ${sessionTitle ?? target.name}`}
            className="flex h-8 min-w-0 items-center gap-2 rounded-[10px] border border-border bg-card px-2.5"
          >
            <h2 className="m-0 min-w-0 shrink truncate text-[13px] font-normal leading-[18px] text-content-strong">
              {sessionTitle ?? target.name}
            </h2>
            {sessionCode ? (
              <Button
                variant="ghost"
                onClick={() => void copySessionId()}
                aria-label={`复制会话 ID ${sessionCode}`}
                title={sessionCode}
                className="h-5 min-w-0 max-w-[180px] shrink-[2] gap-1 rounded-full bg-secondary px-2 text-[11px] font-normal text-foreground/85 transition-colors hover:bg-foreground/10 hover:text-foreground"
              >
                <span className="min-w-0 truncate">{sessionCode}</span>
                <Copy className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
              </Button>
            ) : null}
          </div>
          {/* 连接状态为运行时数据；弱呈现 = 色点（tone 语义）+ 弱灰文字，不与标题抢视觉。 */}
          {connectionLabel ? <ChatConnectionStatus label={connectionLabel} tone={connectionTone} /> : null}
        </div>
      }
    />
  );
}
