/**
 * 发言权条——lease_required 会话在输入区上方渲染的租约状态与操作条。
 *
 * 形态与「用户发言模式」帽条保持同构：一条 26px 高的语义色横条承载
 * 左侧状态说明与右侧主要动作；持有者判定、续期、命令裁决均由 BCS 权威完成。
 */
import { Button } from '@/components/ui';
import type { SpeakingLease, SpeakingLeaseStatus } from '@/domain/collaboration/speakingLease';
import type { GroupSpeakingLeaseModel } from '@/pages/Workspace/hooks/useSpeakingLease';
import { cn } from '@/utils/cn';
import { LoaderCircle, LogOut, Mic, RefreshCw } from 'lucide-react';
import React from 'react';

export type { SpeakingLeaseStatus } from '@/domain/collaboration/speakingLease';

export interface SpeakingLeaseControlProps {
  status: SpeakingLeaseStatus;
  lease?: SpeakingLease;
  /** held 状态下的 1s 步进时钟（毫秒），驱动剩余秒倒计时。 */
  nowMs?: number;
  /** 有指令在途时按钮 loading。 */
  isRequesting?: boolean;
  onAcquire: () => void;
  onPreempt: () => void;
  onRelease: () => void;
}

const statusText: Record<SpeakingLeaseStatus, string> = {
  unheld: '获取caller身份发言',
  held: '已获取caller身份发言',
  occupied: '发言权被其他用户占用',
  error: '发言权状态未知，请重试',
};

const statusClasses: Record<SpeakingLeaseStatus, string> = {
  // 未持有使用与「用户发言模式」一致的 muted 灰色；其余语义色只改变同宽同高的横条。
  unheld: 'bg-muted-foreground/10 text-foreground',
  held: 'bg-success/10 text-success',
  occupied: 'bg-warning/10 text-warning',
  error: 'bg-destructive/10 text-destructive',
};

function actionFor(status: SpeakingLeaseStatus) {
  switch (status) {
    case 'held':
      return { label: '释放', Icon: LogOut, onClick: 'onRelease' } as const;
    case 'occupied':
      return { label: '抢占发言权', Icon: Mic, onClick: 'onPreempt' } as const;
    case 'error':
      return { label: '重试', Icon: RefreshCw, onClick: 'onAcquire' } as const;
    default:
      return { label: '获取发言权', Icon: Mic, onClick: 'onAcquire' } as const;
  }
}

export const SpeakingLeaseControl: React.FC<SpeakingLeaseControlProps> = ({
  status,
  lease,
  nowMs,
  isRequesting,
  onAcquire,
  onPreempt,
  onRelease,
}) => {
  const remainingSeconds =
    status === 'held' && lease
      ? Math.max(0, Math.ceil((lease.expiresAtMs - (nowMs ?? Date.now()) - 1) / 1000))
      : undefined;
  const action = actionFor(status);
  const callbacks = { onAcquire, onPreempt, onRelease } as const;

  return (
    <div
      // 宽高、左右留白与 ComposerStatusBar 相同；色彩用语义 token 随租约状态切换。
      className={cn(
        'mx-auto flex h-[26px] w-[calc(100%-64px)] items-center justify-between rounded-t-xl pl-3 pr-1.5 text-xs',
        statusClasses[status],
      )}
      data-testid="speaking-lease-control"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-current" />
        <span className="shrink-0 font-medium leading-4">caller模式</span>
        <span className="min-w-0 truncate leading-4 opacity-80">
          {statusText[status]}
          {status === 'held' && <span className="ml-2">剩余 {remainingSeconds} 秒</span>}
        </span>
      </div>
      <Button
        size="sm"
        variant="ghost"
        leftIcon={
          isRequesting ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <action.Icon className="h-3.5 w-3.5" aria-hidden />
          )
        }
        disabled={isRequesting || (status === 'held' && !lease)}
        onClick={callbacks[action.onClick]}
        className="h-6 shrink-0 gap-1 rounded-lg px-1.5 text-xs hover:bg-background/60"
      >
        {action.label}
      </Button>
    </div>
  );
};

/**
 * 群聊输入区上方的发言权条装配（模型直通）。
 * 非 lease_required 会话（或模型缺失）渲染 null——普通会话零感知。
 */
export function SpeakingLeaseStrip({ model }: { model?: GroupSpeakingLeaseModel }) {
  if (!model || model.speakingMode !== 'lease_required') return null;
  return (
    <SpeakingLeaseControl
      status={model.speakingLeaseStatus}
      lease={model.speakingLease}
      nowMs={model.leaseNowMs}
      isRequesting={model.isUpdatingSpeakingLease}
      onAcquire={() => void model.acquireSpeakingLease()}
      onPreempt={() => void model.acquireSpeakingLease({ preempt: true })}
      onRelease={() => void (model.speakingLease && model.releaseSpeakingLease(model.speakingLease))}
    />
  );
}
