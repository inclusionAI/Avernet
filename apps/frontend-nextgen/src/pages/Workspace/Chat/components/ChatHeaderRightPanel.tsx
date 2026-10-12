/**
 * ChatHeaderRightPanel —— 对话页头部右缘面板容器 + 会话详情视图（chat-header-panels）。
 *
 * dmore artboard-005 实测：面板 = 右缘 sidebar 320 宽、48px 头行（标题 + 关闭）+ 内容分组；
 * 会话详情 = 只读行（会话名称/会话Key/会话创建时间），字段缺失隐藏行（不占位空白）。
 * 容器复用 ResizableWorkspaceSidebar（与群域 SessionFilesSidebar/会话文件副屏同构容器模式）。
 */
import { IconButton, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import { ResizableWorkspaceSidebar } from '@/pages/Workspace/components/ResizableWorkspaceSidebar';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

export interface ChatHeaderRightPanelProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/** 面板容器：48px 头行（14px 标题 + 关闭钮）+ 内容列。 */
export function ChatHeaderRightPanel({ title, onClose, children }: ChatHeaderRightPanelProps) {
  return (
    <ResizableWorkspaceSidebar
      ariaLabel={`${title}面板`}
      side="right"
      minWidth={320}
      maxWidth={480}
      defaultWidth={320}
      storageKey="teamclaw:chat-header-right-panel-width"
      className="z-30 border-l border-border bg-card"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
          <h3 className="m-0 text-sm font-medium text-foreground">{title}</h3>
          <IconButton label="关闭面板" icon={<X className="size-4" />} variant="ghost" size="sm" onClick={onClose} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">{children}</div>
      </div>
    </ResizableWorkspaceSidebar>
  );
}

export interface SessionDetailPanelProps {
  session: BotChatSessionView | null;
}

/** 只读详情行：label 12px 弱灰 / value 13px；缺失字段整行隐藏；长值截断配项目 Tooltip（门禁禁 title 属性）。 */
function DetailRow({ label, value }: { label: string; value: string | undefined | null }) {
  if (value === undefined || value === null || value.trim() === '') return null;
  return (
    <div className="flex h-[34px] items-center gap-2">
      <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">{value}</span>
          </TooltipTrigger>
          <TooltipContent>{value}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}

/** 会话详情视图（入口=头部「会话管理」）：只读，无任何写操作。 */
export function SessionDetailPanel({ session }: SessionDetailPanelProps) {
  if (!session) {
    return <p className="text-xs text-muted-foreground">未选择会话</p>;
  }
  return (
    <section aria-label="会话详情" className="flex flex-col gap-1">
      <DetailRow label="会话名称" value={session.title} />
      <DetailRow label="会话 Key" value={session.sessionId} />
      <DetailRow label="会话创建时间" value={session.gmtCreate} />
    </section>
  );
}
