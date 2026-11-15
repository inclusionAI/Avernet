import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import type { TodoAcceptanceTarget, TodoArtifact } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { ArrowRight, Bot, Code2, FileText, Link2, Pause } from 'lucide-react';
import {
  artifactPresentation,
  formatInteractionTime,
  interactionContent,
  rejectionQuote,
  rejectionTargets,
  targetKey,
  type InteractionEntry,
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

function ActorHeader({
  actor,
  createdAt,
  botId,
  botName,
  humanName,
  humanAvatarUrl,
}: {
  actor: 'human' | 'bot';
  createdAt: string;
  botId: string;
  botName: string;
  humanName: string;
  humanAvatarUrl?: string;
}) {
  const isBot = actor === 'bot';
  return (
    <div className="flex items-center gap-3">
      {isBot ? (
        <BotDisplayAvatar botId={botId} name={botName} size={40} />
      ) : (
        <Avatar name={humanName} src={humanAvatarUrl} size={40} />
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="truncate text-sm font-semibold text-foreground">{isBot ? botName : humanName}</span>
        {isBot ? <span className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">Bot</span> : null}
        <time className="text-sm tabular-nums text-muted-foreground">{formatInteractionTime(createdAt)}</time>
      </div>
    </div>
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

function ArtifactIcon({ kind }: { kind: 'file' | 'link' | 'code' }) {
  if (kind === 'file') return <FileText className="size-5" aria-hidden />;
  if (kind === 'link') return <Link2 className="size-5" aria-hidden />;
  return <Code2 className="size-5" aria-hidden />;
}

function ArtifactCard({ artifact }: { artifact: Exclude<TodoArtifact, { kind: 'text' }> }) {
  const presentation = artifactPresentation(artifact);
  return (
    <div className="group flex min-h-16 w-full items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 text-left text-foreground transition-colors hover:border-foreground/20 hover:bg-muted/20">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
        <ArtifactIcon kind={presentation.icon} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold">{presentation.title}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{presentation.meta}</span>
      </span>
      <ArrowRight className="size-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden />
    </div>
  );
}

function ArtifactEntry({
  entry,
  botId,
  botName,
  humanName,
  humanAvatarUrl,
  busy,
  onOpenFile,
}: {
  entry: Extract<InteractionEntry, { kind: 'artifact' }>;
  botId: string;
  botName: string;
  humanName: string;
  humanAvatarUrl?: string;
  busy: boolean;
  onOpenFile: (artifact: Extract<TodoArtifact, { kind: 'file' }>) => void;
}) {
  const presentation = artifactPresentation(entry.artifact);
  const card = <ArtifactCard artifact={entry.artifact} />;
  return (
    <article className="min-w-0">
      <ActorHeader
        actor="bot"
        createdAt={entry.artifact.createdAt}
        botId={botId}
        botName={botName}
        humanName={humanName}
        humanAvatarUrl={humanAvatarUrl}
      />
      <div className="mt-3 pl-[52px]">
        {entry.artifact.kind === 'file' ? (
          <Button
            variant="ghost"
            className="block h-auto w-full rounded-xl border-0 bg-transparent p-0 text-left hover:bg-transparent"
            aria-label={`打开产物 ${presentation.title}`}
            disabled={busy}
            onClick={() => onOpenFile(entry.artifact as Extract<TodoArtifact, { kind: 'file' }>)}
          >
            {card}
          </Button>
        ) : (
          <a href={presentation.href || '#'} target="_blank" rel="noreferrer" className="block no-underline">
            {card}
          </a>
        )}
      </div>
    </article>
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
  onToggleTarget: (target: TodoAcceptanceTarget) => void;
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
  onToggleTarget,
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
                <ActorHeader
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
                  <RejectionQuotes
                    targets={rejectionTargets(entry.event)}
                    entryByTarget={entryByTarget}
                    botName={botName}
                    humanName={humanName}
                  />
                </div>
              </article>
            ) : (
              <ArtifactEntry
                entry={entry}
                botId={botId}
                botName={botName}
                humanName={humanName}
                humanAvatarUrl={humanAvatarUrl}
                busy={busyArtifactId === entry.artifact.artifactId}
                onOpenFile={onOpenFile}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
