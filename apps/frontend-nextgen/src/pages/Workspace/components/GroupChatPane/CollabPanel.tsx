import { MessageViewScopeCheckbox } from '@/components/MessageViewScope';
import { Button, Segmented } from '@/components/ui';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/AlertDialog';
import type { MessageViewScope } from '@/domain/collaboration/types';
import type { CollabPanelState } from '@/pages/Workspace/hooks/useCollabPanel';
import { Loader2, UserPlus } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { BotControlRow } from './BotControlRow';

/**
 * 「未加入」底卡（dmore artboard-010 底区 96px：896×72 rounded-8 #FBFBFB 卡 + 112×32 白底钮）：
 * 13px/600 标题 + 11px 副文案（稿件原文），点击走既有 JoinConfirmDialog 二次确认，状态机零改动。
 */
function JoinBar({ joining, onJoin, humanName }: { joining: boolean; onJoin: () => void; humanName: string }) {
  return (
    <div
      role="status"
      aria-label={`${humanName} 暂未加入当前会话`}
      className="flex h-[72px] w-full items-center justify-between rounded-lg bg-muted/40 px-4"
    >
      <div className="min-w-0">
        <p className="m-0 text-[13px] font-semibold leading-[13px] text-foreground">用户暂未加入当前会话</p>
        <p className="m-0 mt-1.5 text-[11px] leading-4 text-zinc-500">以用户身份加入后，可直接发送消息，参与讨论</p>
      </div>
      <Button
        size="sm"
        variant="outline"
        disabled={joining}
        onClick={onJoin}
        className="h-8 w-28 shrink-0 rounded-lg bg-card text-[13px] font-semibold"
      >
        {joining ? <Loader2 className="h-4 w-4 animate-spin" /> : '加入当前会话'}
      </Button>
    </div>
  );
}

/** 「用户协作」已加入态(bot 视角):用户名 + 提示 + 右侧「去发言」；卡式跟随未加入底卡视觉基调(change task 4.4)。 */
function HumanJoinedRow({
  humanName,
  canSwitchToHuman,
  onSwitchToHuman,
}: {
  humanName: string;
  canSwitchToHuman: boolean;
  onSwitchToHuman: () => void;
}) {
  return (
    <div className="flex min-h-[72px] w-full items-center justify-between gap-4 rounded-lg bg-muted/40 px-4">
      <div className="flex min-w-0 items-center gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <UserPlus className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0">
          <p className="m-0 text-[13px] font-semibold leading-tight text-foreground">{humanName}</p>
          <p className="m-0 mt-1.5 text-[11px] leading-4 text-zinc-500">
            当前为 Bot 视角，{humanName}已加入当前会话；点击右侧“去发言”切换到该身份继续发言。
          </p>
        </div>
      </div>
      <Button
        size="sm"
        variant="outline"
        disabled={!canSwitchToHuman}
        onClick={onSwitchToHuman}
        className="h-8 w-20 shrink-0 rounded-lg bg-card text-[13px] font-semibold"
      >
        去发言
      </Button>
    </div>
  );
}

/** 加入会话二次确认（含消息可见域选择，视觉稿：加入当前会话按钮效果.png）。 */
function JoinConfirmDialog({
  open,
  onOpenChange,
  joining,
  onConfirm,
  humanName,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  joining: boolean;
  onConfirm: (scope: MessageViewScope) => void;
  humanName: string;
}) {
  const [participantOnly, setParticipantOnly] = useState(false);
  // 每次打开重置为未勾选（默认完整视角，不做记忆）。
  useEffect(() => {
    if (open) setParticipantOnly(false);
  }, [open]);
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>加入当前会话</AlertDialogTitle>
          <AlertDialogDescription>确认后：您的所有发言将以用户身份（{humanName}）发送。</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-3">
          <MessageViewScopeCheckbox checked={participantOnly} onCheckedChange={setParticipantOnly} disabled={joining} />
          <div className="rounded-lg bg-muted px-3 py-2.5 text-xs leading-5 text-muted-foreground">
            <p className="font-medium text-foreground">其他须知：</p>
            <p className="mt-1">· 您可与主节点Bot进行对话，由主节点Bot负责任务分发、状态推进、质量核验</p>
            <p className="mt-1">· 勾选参与者视角后将过滤协作内部消息；切换到您拥有的 Bot 视角不受影响</p>
          </div>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction onClick={() => onConfirm(participantOnly ? 'participant' : 'full')} disabled={joining}>
            {joining ? '加入中…' : '确认加入'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export interface CollabPanelProps {
  panel: CollabPanelState;
  activeRuns?: ReactNode;
}

/**
 * 协作群会话底部协作面板（对齐 open-claw 我的协作 BottomPanel）：
 * - bot 视角:Bot控制 / 用户协作 双 tab;
 * - human 视角且 human 姿态为 absent:仅「未加入当前会话」提示条。
 */
export function CollabPanel({ panel, activeRuns }: CollabPanelProps) {
  const [tab, setTab] = useState<'bot' | 'human'>('bot');
  const [confirmJoin, setConfirmJoin] = useState(false);

  if (!panel.visible) return null;

  // human 视角 present：发言模式/连接状态已迁 index 的 ComposerStatusBar 帽条（design 决策 7/8）；
  // 本面板仅保留活动 Bot 终止行——「终止入口固定在消息列表外、输入框上方」的既有约束不变。
  const isHumanViewer = !panel.humanAbsentOnly && panel.humanJoined && !panel.botActorId;
  if (isHumanViewer) {
    if (!activeRuns) return null;
    return (
      <div className="border-t border-divider bg-background px-3 pb-1 pt-1.5 sm:px-6" data-testid="collab-panel">
        <div className="mx-auto w-[calc(100%-64px)] pl-3 pr-1.5">{activeRuns}</div>
      </div>
    );
  }

  // human 视角 absent:未加入底卡接管 composer 区（dmore artboard-010 底区，96px≈py-3+72 卡）。
  if (panel.humanAbsentOnly) {
    return (
      <div className="border-t border-divider bg-background px-3 pb-3 pt-3 sm:px-6">
        <JoinBar joining={panel.joining} onJoin={() => setConfirmJoin(true)} humanName={panel.humanName} />
        <JoinConfirmDialog
          open={confirmJoin}
          onOpenChange={setConfirmJoin}
          joining={panel.joining}
          onConfirm={(scope) => {
            void panel.joinSession(scope).then((ok) => {
              if (ok) setConfirmJoin(false);
            });
          }}
          humanName={panel.humanName}
        />
      </div>
    );
  }

  const join = (scope: MessageViewScope) => {
    void panel.joinSession(scope).then((ok) => {
      if (ok) setConfirmJoin(false);
    });
  };

  return (
    <div className="border-t border-divider bg-background px-3 pb-3 pt-2 sm:px-6" data-testid="collab-panel">
      {!panel.identityLocked && (
        <div className="w-56">
          <Segmented
            value={tab}
            onChange={setTab}
            options={[
              { value: 'bot', label: 'Bot控制' },
              { value: 'human', label: '用户协作' },
            ]}
          />
        </div>
      )}
      {/* 内容区固定最小高度并垂直居中,避免 Bot控制/用户协作 切换时因各行文案行数不同导致面板高度来回跳动。 */}
      <div className="flex min-h-[72px] flex-col justify-center">
        {panel.identityLocked || tab === 'bot' ? (
          panel.botMode ? (
            <BotControlRow
              botMode={panel.botMode}
              switching={panel.switchingBotMode}
              onModeChange={(m) => void panel.setBotMode(m)}
            />
          ) : (
            <p className="py-1.5 text-xs text-muted-foreground">当前会话暂不可控制 Bot 发言模式。</p>
          )
        ) : panel.humanJoined ? (
          <HumanJoinedRow
            humanName={panel.humanName}
            canSwitchToHuman={panel.canSwitchToHuman}
            onSwitchToHuman={panel.switchToHuman}
          />
        ) : (
          <JoinBar joining={panel.joining} onJoin={() => setConfirmJoin(true)} humanName={panel.humanName} />
        )}
      </div>
      <JoinConfirmDialog
        open={confirmJoin}
        onOpenChange={setConfirmJoin}
        joining={panel.joining}
        onConfirm={join}
        humanName={panel.humanName}
      />
    </div>
  );
}
