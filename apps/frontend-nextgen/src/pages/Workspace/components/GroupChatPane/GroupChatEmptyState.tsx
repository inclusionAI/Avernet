// 协作群消息区空态：对齐单聊 ChatEmptyState 的「主视觉标记 + 主文案 + 副文案 + 行动」
// 解剖，但每一处描述替换为协作群的真实语义——
// - 主视觉用成员头像簇（Bot 优先、≤3 枚重叠 + 「+n」计数）：协作群的本质是 Bot 与
//   用户多方共场，单头像不足以表达；头像堆承接群列表行已有的语言（重叠步距 + 计数
//   chip），在此放大呈现。不借用消息几何做装饰——预演气泡会被误读为「已有消息在回复」
//   （单聊空态第一轮预览结论，2026-10-12）；
// - 副文案按 group.kind 分流，Manager/Driver 词汇与本消息区 processingLabel 同源，
//   并优先引用真实 Driver/Manager 成员名（按 role 匹配 session.participants）；
// - 行动区只保留真实装配的能力：human 视角下点击「点名成员」写入 '@'——Sender 的
//   insert 走 commitValue→mentionHandlers.onValueChange，成员面板随之自然唤起，与
//   单聊空态插 '/file' 唤起命令面板为同一机制（mentionConfig 仅 user 视角装配，
//   未装配即不渲染）；Bot 视角无输入框，不渲染行动区，主文案也改述现状——
//   「发送一条消息」对 Bot 是误导，Bot 发言由协作面板按发言模式控制。
import { Avatar, Button } from '@/components/ui';
import type { GroupView, SessionView } from '@/domain/collaboration';
import { AtSign } from 'lucide-react';

/** 头像簇最多展示的成员数，其余并入「+n」。 */
const CLUSTER_MAX = 3;

interface GroupChatEmptyStateProps {
  group: GroupView;
  session: SessionView;
  /** 当前视角身份：user=人类视角（有输入框）；bot=Bot 视角（由协作面板发言）；缺省视为 user。 */
  viewerKind?: 'user' | 'bot' | null;
  /** @ 成员面板是否已装配（human 视角经 buildGroupMentionConfig 判定）；未装配不渲染点名卡。 */
  canMention?: boolean;
  /** 点击点名卡的落地动作：写入 '@' 并聚焦（宿主接 inputRef）。 */
  onInsertDraft?: (text: string) => void;
}

/** 头像簇取材：Bot 排前（Bot 是群内响应者），human 靠后；组内保持 participants 原有顺序。 */
function pickClusterParticipants(participants: SessionView['participants']) {
  const bots = participants.filter((participant) => participant.kind === 'bot');
  const humans = participants.filter((participant) => participant.kind !== 'bot');
  return [...bots, ...humans].slice(0, CLUSTER_MAX);
}

/** 副文案：按群类型描述「谁来响应」，Manager/Driver 词汇与 processingLabel 同源。 */
function describeGroup(group: GroupView, participants: SessionView['participants']): string {
  const coordinator = participants.find((participant) =>
    group.kind === 'task_master_slave' ? participant.role === 'manager' : participant.role === 'driver',
  )?.name;
  if (group.kind === 'task_master_slave') {
    return `${coordinator || 'Manager'} 会理解群聊目标，并调度成员分工推进`;
  }
  if (group.kind === 'free_chat') {
    return `${coordinator || 'Driver'} 会理解群聊目标，并协调成员共同回应`;
  }
  return '成员将按任务编排推进协作';
}

export function GroupChatEmptyState({ group, session, viewerKind, canMention, onInsertDraft }: GroupChatEmptyStateProps) {
  const isBotViewer = viewerKind === 'bot';
  const participants = session.participants ?? [];
  const cluster = pickClusterParticipants(participants);
  const total = session.participantCount ?? participants.length;
  const extra = Math.max(total - cluster.length, 0);
  const showMentionOpener = !isBotViewer && !!canMention && !!onInsertDraft;

  return (
    <div className="flex w-full max-w-md flex-col items-center gap-1 text-center">
      {cluster.length > 0 ? (
        <div className="flex items-center -space-x-3" aria-hidden="true">
          {cluster.map((participant) => (
            <span
              key={participant.actorId}
              className="flex size-10 shrink-0 items-center justify-center rounded-full ring-4 ring-background"
            >
              <Avatar name={participant.name} src={participant.avatarUrl} size={40} />
            </span>
          ))}
          {extra > 0 ? (
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-medium text-muted-foreground ring-4 ring-background">
              +{extra}
            </span>
          ) : null}
        </div>
      ) : null}
      <p className="m-0 mt-3 text-sm font-medium text-foreground">
        {isBotViewer ? '协作群对话尚未开始' : '发送一条消息开始协作群对话'}
      </p>
      <p className="m-0 max-w-sm text-[13px] leading-6 text-muted-foreground">
        {isBotViewer ? '群内出现相关消息后，Bot 将按各自的发言模式参与回应' : describeGroup(group, participants)}
      </p>
      {showMentionOpener && onInsertDraft ? (
        <div className="mt-6 flex w-full max-w-sm flex-col gap-2">
          <p className="m-0 text-xs text-muted-foreground">试试这样开场</p>
          <Button
            variant="outline"
            onClick={() => onInsertDraft('@')}
            className="flex h-auto w-full items-center justify-start gap-3 rounded-xl border-border bg-background px-4 py-3 font-normal text-left hover:border-primary/40 hover:bg-selected"
          >
            <span
              className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
              aria-hidden="true"
            >
              <AtSign className="h-4 w-4" />
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-[13px] text-content-strong">点名成员</span>
              <span className="text-xs text-muted-foreground">指定参与的成员，或用 ALL-Bots 点名全部 Bot</span>
            </span>
            <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">@</code>
          </Button>
          <p className="m-0 mt-1 text-xs text-muted-foreground">
            也可以直接发送；工具栏「文件」可上传会话文件，图片支持粘贴
          </p>
        </div>
      ) : null}
    </div>
  );
}