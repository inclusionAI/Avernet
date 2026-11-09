/**
 * ChatHeaderRightPanel —— 对话页头部右缘内联副屏容器 + 会话详情视图（chat-header-panels）。
 *
 * 面板共用聊天右栏宽度，48px 头行（标题 + 关闭）+ 内容分组；
 * 会话详情 = 只读行（会话名称/会话 ID/会话创建时间;2026-10-14 口径统一:原稿"会话Key"与值 session ID 不符），字段缺失隐藏行（不占位空白）。
 * 容器复用 ResizableWorkspaceSidebar(side=right)，以对话区内联展开/收起呈现——对话区随面板
 * 让宽、无抽屉蒙层，与 SDK 副屏同构；open=false 时宽度收 0（面板保持挂载以获得开合过渡）。
 */
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { BotChatSessionView } from '@/services/workspace/botSessionService';

export { ChatHeaderRightPanel, type ChatHeaderRightPanelProps } from '../../components/WorkspaceHeaderRightPanel';

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
      <DetailRow label="会话 ID" value={session.sessionId} />
      <DetailRow label="会话创建时间" value={session.gmtCreate} />
    </section>
  );
}
