import type { SessionStatusPresentation } from '@/domain/conversation/sessionStatusProtocol';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { useSessionStatusStore } from '@/stores/sessionStatusStore';
import { cn } from '@/utils/cn';
import {
  BellRing,
  CircleDashed,
  CircleSlash,
  Loader2,
  MessageCircleCheck,
  MessageCircleWarning,
  PauseCircle,
  type LucideIcon,
} from 'lucide-react';

interface SessionStatusIconVisual {
  Icon: LucideIcon;
  colorClass: string;
  animationClass?: string;
}

/**
 * Coding Bot 专用 DevFlow 状态图标：语义与 AIXCoding 的
 * `getSessionStatusIconVisual` 对齐，但颜色改用 TeamClaw 的语义 token；
 * 公共宿主只渲染图标，不在桥接协议里传递 Lucide 组件或 CSS 类。
 */
const SESSION_STATUS_ICON_MAP: Record<string, SessionStatusIconVisual> = {
  running: { Icon: Loader2, colorClass: 'text-info', animationClass: 'animate-spin' },
  waitinghuman: { Icon: BellRing, colorClass: 'text-warning', animationClass: 'bell-wiggle' },
  completed: { Icon: MessageCircleCheck, colorClass: 'text-success' },
  completedfailed: { Icon: MessageCircleWarning, colorClass: 'text-destructive' },
  failed: { Icon: MessageCircleWarning, colorClass: 'text-destructive' },
  paused: { Icon: PauseCircle, colorClass: 'text-warning' },
  interrupted: { Icon: PauseCircle, colorClass: 'text-warning' },
  aborted: { Icon: CircleSlash, colorClass: 'text-muted-foreground' },
  abortedplatformrestart: { Icon: CircleSlash, colorClass: 'text-muted-foreground' },
  skipped: { Icon: CircleDashed, colorClass: 'text-content-hint' },
  notreached: { Icon: CircleDashed, colorClass: 'text-content-hint' },
  pending: { Icon: CircleDashed, colorClass: 'text-content-hint' },
};

/** The status slot stays 14x14; the row keeps a fixed title inset even when no status is rendered. */
const SESSION_STATUS_SLOT_CLASS =
  'absolute left-[40px] top-1/2 z-10 size-3.5 flex items-center justify-center -translate-y-1/2';
const SESSION_STATUS_ICON_CLASS = 'size-3.5';
const REPLYING_ICON: SessionStatusIconVisual = {
  Icon: Loader2,
  colorClass: 'text-muted-foreground',
  animationClass: 'animate-spin',
};


export interface ConversationSessionStatusProps {
  botId: string;
  sessionId: string;
  /** 普通 Runtime 正在处理当前会话；仅在没有 Coding 状态时降级显示原 loading。 */
  showReplying?: boolean;
}

export function ConversationSessionStatus({ botId, sessionId, showReplying = false }: ConversationSessionStatusProps) {
  const status = useSessionStatusStore((state): SessionStatusPresentation | undefined =>
    state.botId === botId && state.ready && state.snapshotReady ? state.statuses[sessionId] : undefined,
  );
  const statusGateReady = useSessionStatusStore((state) => state.botId === botId && state.ready);
  const statusSnapshotReady = useSessionStatusStore((state) => state.botId === botId && state.snapshotReady);
  let statusData = 'unknown';
  if (status) {
    const normalizedCode = status.code.trim().toLowerCase();
    statusData = normalizedCode === 'idle' ? 'idle' : normalizedCode;
  }

  const renderReplying = () => {
    if (!showReplying) return null;
    const { Icon } = REPLYING_ICON;
    return (
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              role="status"
              aria-label="Bot 正在回复"
              data-session-status={statusData}
              data-status-ready={statusGateReady}
              data-snapshot-ready={statusSnapshotReady}
              className={SESSION_STATUS_SLOT_CLASS}
            >
              <Icon className={cn(SESSION_STATUS_ICON_CLASS, 'animate-spin', REPLYING_ICON.colorClass)} aria-hidden="true" />
            </span>
          </TooltipTrigger>
          <TooltipContent>Bot 正在回复</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  };
  if (!status) return renderReplying();

  const normalizedCode = status.code.trim().toLowerCase();
  if (normalizedCode === 'idle') return renderReplying();

  const visual = SESSION_STATUS_ICON_MAP[normalizedCode];
  if (!visual) return renderReplying();
  const { Icon, colorClass, animationClass } = visual;
  const accessibleLabel = `DevFlow：${status.label}`;
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="status"
            aria-label={accessibleLabel}
            data-session-status={statusData}
            data-status-ready={statusGateReady}
            data-snapshot-ready={statusSnapshotReady}
            className={SESSION_STATUS_SLOT_CLASS}
          >
            <Icon className={cn(SESSION_STATUS_ICON_CLASS, colorClass, animationClass)} aria-hidden="true" />
          </span>
        </TooltipTrigger>
        <TooltipContent>{accessibleLabel}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
