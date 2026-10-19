import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';

/** 对话与协作群顶栏共用：语义色点 + 弱灰连接文案。 */
export const CONNECTION_TONE_DOT = {
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-destructive',
  neutral: 'bg-muted-foreground/40',
} as const;

/**
 * 窄宽收纳（2026-10-19 用户反馈：<lg 视口「已连接」挤占页头）：仅 success 正常态的
 * 文案 span 缺省收纳（hidden + 视口/容器可见类）——正常态无需解释，绿点即语义；
 * 警示/错误/中性态保留完整文案与语义色点（连接失败需要被读到），任何宽度不收纳、
 * 也不全体退化为绿点。可访问性不随视觉收纳缺失：role=status 的可访问名恒取
 * aria-label（读屏窄宽下仍有文字）；悬停提示走 Radix Tooltip（200ms，同
 * ComposerStatusBar 传递型预设；TC-G006 禁用原生 title），Portal 渲染零占位。
 *
 * labelVisibleClassName 整串替换缺省 'lg:inline' 的「何时可见」侧：GroupHeader 传入
 * 'lg:@min-[32.5rem]/group-header:inline'（视口 ≥lg 且群头命名容器 ≥ 断点才显示），
 * 使桌面端副屏/管理面板挤压主区时同样仅保绿点；单聊页头无命名容器，沿用缺省。
 */
export function ChatConnectionStatus({
  label,
  tone,
  labelVisibleClassName = 'lg:inline',
}: {
  label: string;
  tone: keyof typeof CONNECTION_TONE_DOT;
  /** 已连接文案的可见断点类：caller 可换成命名容器并查变体（见上）；仅 success 态生效。 */
  labelVisibleClassName?: string;
}) {
  // 仅「已连接」正常态参与收纳；其余状态原样渲染（不只绿点、不丢警示语义）。
  const labelClassName = tone === 'success' ? `hidden ${labelVisibleClassName}` : undefined;
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="status"
            aria-label={label}
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground"
          >
            <span aria-hidden className={`size-1.5 shrink-0 rounded-full ${CONNECTION_TONE_DOT[tone]}`} />
            <span className={labelClassName}>{label}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}