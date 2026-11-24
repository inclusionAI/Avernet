import { Button } from '@/components/ui/Button';
import type { TodoAcceptanceTarget, TodoArtifact } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { Bot, Pause, Reply } from 'lucide-react';
import { TodoInteractionActorHeader } from './TodoInteractionActorHeader';
import { TodoInteractionArtifactEntry } from './TodoInteractionArtifactEntry';
import {
  formatInteractionTime,
  interactionContent,
  replyToEventId,
  rejectionQuote,
  rejectionTargets,
  targetKey,
  type InteractionEntry,
  type InteractionReplyReference,
  type TargetEntry,
} from './todoInteractionModel';

function RejectionQuotes({
  targets,
  entryByTarget,
  botName,
  humanName,
}: {
  targets: TodoAcceptanceTarget[];
  entryByTarget: Map<string, TargetEntry>;
  botName: string;
  humanName: string;
}) {
  if (targets.length === 0) return null;
  return (
    <div className="mt-3 space-y-2" role="group" aria-label="问题引用">
      {targets.map((target) => {
        const quote = rejectionQuote(target, entryByTarget, botName, humanName);
        return (
          <blockquote
            key={targetKey(target)}
            className="m-0 rounded-r-lg border-l-2 border-destructive/55 bg-muted/45 px-3 py-2"
          >
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <span className="text-destructive">f{target.floor}</span>
              <span aria-hidden>·</span>
              <span>{quote.label}</span>
            </div>
            <p className="m-0 mt-1 line-clamp-2 break-words text-xs leading-5 text-foreground/80">{quote.content}</p>
          </blockquote>
        );
      })}
    </div>
  );
}

function ReplyQuote({ reference }: { reference?: InteractionReplyReference }) {
  return (
    <blockquote className="m-0 mt-3 rounded-r-lg border-l-2 border-primary/45 bg-muted/45 px-3 py-2">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span className="text-primary">{reference ? `f${reference.floor}` : '原互动'}</span>
        {reference ? <span aria-hidden>·</span> : null}
        {reference ? <span>{reference.label}</span> : null}
      </div>
      <p className="m-0 mt-1 line-clamp-2 break-words text-xs leading-5 text-foreground/80">
        {reference?.content ?? '原内容暂不可用'}
      </p>
    </blockquote>
  );
}

function ReplyAction({
  floor,
  disabled,
  onClick,
}: {
  floor: number;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      className="mt-2 h-7 rounded-md px-2 text-xs text-muted-foreground"
      aria-label={`回复 f${floor}`}
      disabled={disabled}
      onClick={onClick}
      leftIcon={<Reply className="size-3.5" aria-hidden />}
    >
      回复
    </Button>
  );
}

function QuestionMarker({
  active,
  enabled,
  floor,
  onClick,
}: {
  active: boolean;
  enabled: boolean;
  floor: number;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={active ? `取消 f${floor} 问题点标记` : `标记 f${floor} 为问题点`}
      aria-pressed={active}
      disabled={!enabled}
      className={cn(
        'h-full min-h-12 w-1 rounded-full border-0 p-0 hover:bg-destructive/45 disabled:opacity-100',
        active ? 'bg-destructive hover:bg-destructive' : enabled ? 'bg-border' : 'bg-transparent',
      )}
      onClick={onClick}
    />
  );
}

function NoticeRow({ entry }: { entry: Extract<InteractionEntry, { kind: 'notice' }> }) {
  const Icon = entry.noticeType === 'started' ? Bot : Pause;
  return (
    <div className="flex justify-center py-1">
      <div className="inline-flex items-center gap-2 rounded-full bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
        <span>{entry.content}</span>
        <span>· {formatInteractionTime(entry.createdAt)}</span>
      </div>
    </div>
  );
}

interface TodoInteractionStreamProps {
  entries: InteractionEntry[];
  entryByTarget: Map<string, TargetEntry>;
  selectedTargetKeys: Set<string>;
  rejectionMode: boolean;
  botId: string;
  botName: string;
  humanName: string;
  humanAvatarUrl?: string;
  busyArtifactId: string | null;
  replyReferences: Map<string, InteractionReplyReference>;
  replyEnabled: boolean;
  onToggleTarget: (target: TodoAcceptanceTarget) => void;
  onReply: (target: InteractionReplyReference) => void;
  onOpenFile: (artifact: Extract<TodoArtifact, { kind: 'file' }>) => void;
}

export function TodoInteractionStream({
  entries,
  entryByTarget,
  selectedTargetKeys,
  rejectionMode,
  botId,
  botName,
  humanName,
  humanAvatarUrl,
  busyArtifactId,
  replyReferences,
  replyEnabled,
  onToggleTarget,
  onReply,
  onOpenFile,
}: TodoInteractionStreamProps) {
  if (entries.length === 0) {
    return <p className="m-0 py-8 text-center text-sm text-muted-foreground">暂无互动记录。</p>;
  }
  return (
    <ol className="m-0 list-none p-0">
      {entries.map((entry, index) => {
        const floor = index + 1;
        const divider = index < entries.length - 1 ? 'border-b border-divider' : '';
        if (entry.kind === 'notice') {
          return (
            <li key={entry.id} className={cn('py-5', divider)} data-floor={floor}>
              <NoticeRow entry={entry} />
            </li>
          );
        }

        const target: TodoAcceptanceTarget = {
          kind: entry.kind === 'message' ? 'event' : 'artifact',
          id: entry.kind === 'message' ? entry.event.eventId : entry.artifact.artifactId,
          floor,
        };
        const replyReference =
          entry.kind === 'message'
            ? replyReferences.get(entry.event.eventId)
            : entry.event
              ? replyReferences.get(entry.event.eventId)
              : undefined;
        const persistedReplyEventId = entry.kind === 'message' ? replyToEventId(entry.event) : undefined;
        const persistedReplyReference = persistedReplyEventId
          ? replyReferences.get(persistedReplyEventId)
          : undefined;
        return (
          <li
            key={entry.id}
            className={cn('grid grid-cols-[4px_minmax(0,1fr)] gap-4 py-6', divider)}
            data-floor={floor}
          >
            <QuestionMarker
              active={selectedTargetKeys.has(targetKey(target))}
              enabled={rejectionMode}
              floor={floor}
              onClick={() => onToggleTarget(target)}
            />
            {entry.kind === 'message' ? (
              <article className="min-w-0">
                <TodoInteractionActorHeader
                  actor={entry.event.actorType === 'bot' ? 'bot' : 'human'}
                  createdAt={entry.event.createdAt}
                  botId={botId}
                  botName={botName}
                  humanName={humanName}
                  humanAvatarUrl={humanAvatarUrl}
                />
                <div className="pl-[52px]">
                  {entry.event.eventType === 'task.rejected' ? (
                    <p className="m-0 mt-3 text-xs font-medium text-destructive">验收不通过</p>
                  ) : null}
                  <p className="m-0 mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
                    {interactionContent(entry.event)}
                  </p>
                  {persistedReplyEventId ? <ReplyQuote reference={persistedReplyReference} /> : null}
                  <RejectionQuotes
                    targets={rejectionTargets(entry.event)}
                    entryByTarget={entryByTarget}
                    botName={botName}
                    humanName={humanName}
                  />
                  {replyReference ? (
                    <ReplyAction
                      floor={floor}
                      disabled={!replyEnabled}
                      onClick={() => onReply(replyReference)}
                    />
                  ) : null}
                </div>
              </article>
            ) : (
              <div className="min-w-0">
                <TodoInteractionArtifactEntry
                  entry={entry}
                  botId={botId}
                  botName={botName}
                  humanName={humanName}
                  humanAvatarUrl={humanAvatarUrl}
                  busy={busyArtifactId === entry.artifact.artifactId}
                  onOpenFile={onOpenFile}
                />
                {replyReference ? (
                  <div className="pl-[52px]">
                    <ReplyAction
                      floor={floor}
                      disabled={!replyEnabled}
                      onClick={() => onReply(replyReference)}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
