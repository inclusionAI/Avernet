import { IconButton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { Copy } from 'lucide-react';
import { toast } from 'sonner';

/**
 * GroupHeader 身份胶囊内的会话 ID 标签（dmore artboard-003）：短号常驻（超宽截断，
 * 悬停 Tooltip 预览全量），复制图标写剪贴板并 toast 反馈（剪贴板不可用时 toast 失败兜底）。
 * 从 GroupHeader 原位抽出（组件 300 行守卫），行为与样式零改动；单聊页头的复制胶囊
 * 结构不同（ChatPanelHeader 自有实现），不共用。
 */
export function GroupSessionIdPill({ sessionId }: { sessionId: string }) {
  const copySessionId = async () => {
    // 抽取前原守卫（GroupHeader copySessionKey）原样保留：ID 缺失时静默不复制。
    if (!sessionId) return;
    try {
      await navigator.clipboard.writeText(sessionId);
      toast.success('会话 ID 已复制');
    } catch {
      toast.error('复制失败，请手动复制。');
    }
  };

  return (
    <TooltipProvider>
      <span className="flex h-5 min-w-10 max-w-[180px] shrink-[2] items-center gap-1 rounded-full bg-secondary px-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="min-w-0 flex-1 truncate text-[11px] leading-[16.5px] text-foreground/85">
              {sessionId}
            </span>
          </TooltipTrigger>
          <TooltipContent>{sessionId}</TooltipContent>
        </Tooltip>
        <IconButton
          label="复制会话 ID"
          icon={<Copy className="h-3 w-3" aria-hidden />}
          size="sm"
          variant="ghost"
          onClick={() => void copySessionId()}
          className="h-5 w-5 shrink-0 rounded-full text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
        />
      </span>
    </TooltipProvider>
  );
}