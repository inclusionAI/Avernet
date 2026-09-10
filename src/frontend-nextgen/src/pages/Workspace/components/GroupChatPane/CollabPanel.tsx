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
import { useEffect, useState } from 'react';
import { BotControlRow } from './BotControlRow';
import { LeaveBar } from './LeaveBar';

/** 「未加入当前会话」提示条（human absent / 用户协作未加入态共用,对齐 open-claw 图三样式）。 */
function JoinBar({ joining, onJoin, humanName }: { joining: boolean; onJoin: () => void; humanName: string }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted">
          <UserPlus className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium leading-tight text-foreground">未加入当前会话</p>
          <p className="mt-0.5 text-xs leading-tight text-muted-foreground">
            以用户身份加入当前会话后，{humanName}可直接发言
          </p>
        </div>
      </div>
      <Button size="sm" variant="default" disabled={joining} onClick={onJoin} className="shrink-0">
        {joining ? <Loader2 className="h-4 w-4 animate-spin" /> : '加入当前会话'}
      </Button>
    </div>
  );
}

/** 「用户协作」已加入态(bot 视角):用户名 + 提示 + 右侧「去发言」。 */
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
    <div className="flex items-center justify-between py-1.5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <UserPlus className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium leading-tight text-foreground">{humanName}</p>
          <p className="mt-0.5 line-clamp-2 text-xs leading-tight text-muted-foreground">
            当前为 Bot 视角，{humanName}已加入当前会话；点击右侧“去发言”切换到该身份继续发言。
          </p>
        </div>
      </div>
      <Button size="sm" variant="default" disabled={!canSwitchToHuman} onClick={onSwitchToHuman} className="shrink-0">
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
}

/**
 * 协作群会话底部协作面板（对齐 open-claw 我的协作 BottomPanel）：
 * - bot 视角:Bot控制 / 用户协作 双 tab;
 * - human 视角且 human 姿态为 absent:仅「未加入当前会话」提示条。
 */
export function CollabPanel({ panel }: CollabPanelProps) {
  const [tab, setTab] = useState<'bot' | 'human'>('bot');
  const [confirmJoin, setConfirmJoin] = useState(false);
  const [leaving, setLeaving] = useState(false);

  if (!panel.visible) return null;

  // human 视角 present:仅显示「在会话中隐身」条，聊天输入框仍正常渲染。
  const isHumanViewer = !panel.humanAbsentOnly && panel.humanJoined && !panel.botActorId;
  if (isHumanViewer) {
    const handleLeave = () => {
      setLeaving(true);
      void panel.leaveSession().finally(() => setLeaving(false));
    };
    return (
      <div className="border-t border-border bg-background px-3 pb-2 pt-2 sm:px-6">
        <LeaveBar
          humanName={panel.humanName}
          onLeave={handleLeave}
          leaving={leaving}
          viewScope={panel.humanViewScope}
          switchingViewScope={panel.switchingViewScope}
          onViewScopeChange={(scope) => void panel.setViewScope(scope)}
        />
      </div>
    );
  }

  // human 视角 absent:仅显示加入条(第三张设计图)。
  if (panel.humanAbsentOnly) {
    return (
      <div className="border-t border-border bg-background px-3 pb-3 pt-2 sm:px-6">
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
    <div className="border-t border-border bg-background px-3 pb-3 pt-2 sm:px-6" data-testid="collab-panel">
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
      {/* 内容区固定最小高度并垂直居中,避免 Bot控制/用户协作 切换时因各行文案行数不同导致面板高度来回跳动。 */}
      <div className="flex min-h-[72px] flex-col justify-center">
        {tab === 'bot' ? (
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
