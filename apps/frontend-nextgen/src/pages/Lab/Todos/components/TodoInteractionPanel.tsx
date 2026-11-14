import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import type { TodoAcceptanceTarget, TodoArtifact, TodoEvent } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { ArrowRight, Bot, Code2, FileText, Link2, MessageSquarePlus, Pause, Send } from 'lucide-react';
import { useMemo, useState } from 'react';

interface TodoInteractionPanelProps {
  interactions: TodoEvent[];
  events: TodoEvent[];
  artifacts: TodoArtifact[];
  botId: string;
  botName?: string;
  humanName?: string;
  humanAvatarUrl?: string;
  enabled: boolean;
  loading: boolean;
  rejectionMode: boolean;
  selectedTargets: TodoAcceptanceTarget[];
  rejectionMessage: string;
  onToggleTarget: (target: TodoAcceptanceTarget) => void;
  onRejectionMessageChange: (message: string) => void;
  onCancelRejection: () => void;
  onSubmitRejection: () => Promise<void> | void;
  onReadArtifact: (artifactId: string, disposition: 'inline' | 'attachment') => Promise<Blob>;
  onSubmit: (message: string) => Promise<boolean>;
}

type InteractionEntry =
  | { kind: 'message'; id: string; event: TodoEvent }
  | { kind: 'artifact'; id: string; artifact: Exclude<TodoArtifact, { kind: 'text' }> }
  | { kind: 'notice'; id: string; content: string; createdAt: string; noticeType: 'started' | 'completed' };

function formatTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function eventKey(item: TodoEvent): string {
  return item.eventId || `${item.executionId}-${item.eventSeq}`;
}

function payloadContent(item: TodoEvent): string {
  return typeof item.payload.content === 'string' ? item.payload.content : '';
}

function interactionContent(item: TodoEvent): string {
  if (item.content) return item.content;
  const content = payloadContent(item);
  if (content) return content;
  if (item.eventType === 'task.completed') return '本轮执行已完成，但未上报文本结果。';
  return item.eventType;
}

function rejectionTargetFloors(item: TodoEvent): number[] {
  if (item.eventType !== 'task.rejected' || !Array.isArray(item.payload.targets)) return [];
  return item.payload.targets
    .map((target) => {
      if (!target || typeof target !== 'object') return null;
      const floor = (target as Record<string, unknown>).floor;
      return typeof floor === 'number' && Number.isInteger(floor) && floor > 0 ? floor : null;
    })
    .filter((floor): floor is number => floor !== null)
    .sort((left, right) => left - right);
}

function selectedFloors(targets: TodoAcceptanceTarget[]): string {
  return [...targets]
    .sort((left, right) => left.floor - right.floor)
    .map((target) => `f${target.floor}`)
    .join('、');
}

function compareEvents(left: TodoEvent, right: TodoEvent): number {
  if (left.eventSeq !== right.eventSeq) return left.eventSeq - right.eventSeq;
  return new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime();
}

function buildEntries(
  interactions: TodoEvent[],
  events: TodoEvent[],
  artifacts: TodoArtifact[],
  botName: string,
): InteractionEntry[] {
  const enrichedInteractions = new Map(interactions.map((item) => [eventKey(item), item]));
  const stream = new Map<string, TodoEvent>();
  events.forEach((item) => stream.set(eventKey(item), enrichedInteractions.get(eventKey(item)) ?? item));
  interactions.forEach((item) => stream.set(eventKey(item), item));

  const nonTextArtifacts = artifacts.filter(
    (artifact): artifact is Exclude<TodoArtifact, { kind: 'text' }> => artifact.kind !== 'text',
  );
  const renderedArtifactIds = new Set<string>();
  const entries: InteractionEntry[] = [];

  [...stream.values()].sort(compareEvents).forEach((item) => {
    const key = eventKey(item);
    if (
      item.eventType === 'task.created' ||
      item.eventType === 'user_interaction.posted' ||
      item.eventType === 'task.rejected'
    ) {
      entries.push({ kind: 'message', id: `message-${key}`, event: item });
      return;
    }
    if (item.eventType === 'task.started') {
      entries.push({
        kind: 'notice',
        id: `notice-${key}`,
        content: `${botName} 开始处理`,
        createdAt: item.createdAt,
        noticeType: 'started',
      });
      return;
    }
    if (item.eventType !== 'task.completed') return;

    entries.push({ kind: 'message', id: `message-${key}`, event: item });
    nonTextArtifacts
      .filter((artifact) => artifact.executionId === item.executionId)
      .sort((left, right) => new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime())
      .forEach((artifact) => {
        renderedArtifactIds.add(artifact.artifactId);
        entries.push({ kind: 'artifact', id: `artifact-${artifact.artifactId}`, artifact });
      });
    entries.push({
      kind: 'notice',
      id: `notice-completed-${key}`,
      content: '转为待处理，等待发起人验收',
      createdAt: item.createdAt,
      noticeType: 'completed',
    });
  });

  nonTextArtifacts
    .filter((artifact) => !renderedArtifactIds.has(artifact.artifactId))
    .sort((left, right) => new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime())
    .forEach((artifact) => entries.push({ kind: 'artifact', id: `artifact-${artifact.artifactId}`, artifact }));

  return entries;
}

function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '未知大小';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function artifactPresentation(artifact: Exclude<TodoArtifact, { kind: 'text' }>) {
  if (artifact.kind === 'file') {
    const extension = artifact.content.fileName.split('.').pop()?.toLowerCase() || '文件';
    return {
      icon: <FileText className="size-5" aria-hidden />,
      title: artifact.content.fileName || artifact.artifactName,
      meta: `${extension} · ${formatFileSize(artifact.content.size)}`,
      href: null,
    };
  }
  if (artifact.kind === 'url_link') {
    return {
      icon: <Link2 className="size-5" aria-hidden />,
      title: artifact.artifactName,
      meta: artifact.content.type || '外部链接',
      href: artifact.content.url,
    };
  }
  return {
    icon: <Code2 className="size-5" aria-hidden />,
    title: artifact.content.codeBaseName || artifact.artifactName,
    meta: artifact.content.prLink ? 'Pull Request' : '代码产物',
    href: artifact.content.prLink || artifact.content.codeBaseAddress,
  };
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
    <button
      type="button"
      aria-label={active ? `取消 f${floor} 问题点标记` : `标记 f${floor} 为问题点`}
      aria-pressed={active}
      disabled={!enabled}
      className={cn(
        'h-full min-h-12 w-1 rounded-full border-0 p-0 transition-colors',
        active ? 'bg-destructive' : enabled ? 'bg-transparent hover:bg-destructive/35' : 'bg-transparent',
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
        <time className="text-sm tabular-nums text-muted-foreground">{formatTime(createdAt)}</time>
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
        <span>· {formatTime(entry.createdAt)}</span>
      </div>
    </div>
  );
}

export function TodoInteractionPanel({
  interactions,
  events,
  artifacts,
  botId,
  botName,
  humanName = '我',
  humanAvatarUrl,
  enabled,
  loading,
  rejectionMode,
  selectedTargets,
  rejectionMessage,
  onToggleTarget,
  onRejectionMessageChange,
  onCancelRejection,
  onSubmitRejection,
  onReadArtifact,
  onSubmit,
}: TodoInteractionPanelProps) {
  const [message, setMessage] = useState('');
  const [busyArtifactId, setBusyArtifactId] = useState<string | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const resolvedBotName = botName?.trim() || botId || 'Bot';
  const entries = useMemo(
    () => buildEntries(interactions, events, artifacts, resolvedBotName),
    [artifacts, events, interactions, resolvedBotName],
  );
  const selectedTargetKeys = useMemo(
    () => new Set(selectedTargets.map((target) => `${target.kind}:${target.id}`)),
    [selectedTargets],
  );
  const floors = selectedFloors(selectedTargets);

  const submit = async () => {
    const success = await onSubmit(message);
    if (success) setMessage('');
  };
  const openFileArtifact = async (artifact: Extract<TodoArtifact, { kind: 'file' }>) => {
    setBusyArtifactId(artifact.artifactId);
    setArtifactError(null);
    try {
      const blob = await onReadArtifact(artifact.artifactId, 'inline');
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.target = '_blank';
      anchor.rel = 'noreferrer';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (cause) {
      setArtifactError(cause instanceof Error ? cause.message : '产物文件读取失败');
    } finally {
      setBusyArtifactId(null);
    }
  };

  return (
    <section role="region" aria-label="待办互动">
      <div className="flex flex-wrap items-center gap-2 border-b border-divider pb-5">
        <MessageSquarePlus className="size-5 text-foreground" aria-hidden />
        <h2 className="m-0 text-lg font-semibold text-foreground">互动</h2>
        <span className="text-sm text-muted-foreground">({entries.length})</span>
        {rejectionMode ? <span className="ml-2 text-sm text-destructive">· 点楼层左侧竖条标记问题点</span> : null}
      </div>

      {entries.length > 0 ? (
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

            if (entry.kind === 'message') {
              const actor = entry.event.actorType === 'bot' ? 'bot' : 'human';
              const target: TodoAcceptanceTarget = { kind: 'event', id: entry.event.eventId, floor };
              const rejectionFloors = rejectionTargetFloors(entry.event);
              return (
                <li
                  key={entry.id}
                  className={cn('grid grid-cols-[4px_minmax(0,1fr)] gap-4 py-6', divider)}
                  data-floor={floor}
                >
                  <QuestionMarker
                    active={selectedTargetKeys.has(`event:${entry.event.eventId}`)}
                    enabled={rejectionMode}
                    floor={floor}
                    onClick={() => onToggleTarget(target)}
                  />
                  <article className="min-w-0">
                    <ActorHeader
                      actor={actor}
                      createdAt={entry.event.createdAt}
                      botId={botId}
                      botName={resolvedBotName}
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
                      {rejectionFloors.length > 0 ? (
                        <p className="m-0 mt-2 text-xs text-muted-foreground">
                          问题楼层：{rejectionFloors.map((item) => `f${item}`).join('、')}
                        </p>
                      ) : null}
                    </div>
                  </article>
                </li>
              );
            }

            const presentation = artifactPresentation(entry.artifact);
            const fileArtifact = entry.artifact.kind === 'file' ? entry.artifact : null;
            const target: TodoAcceptanceTarget = { kind: 'artifact', id: entry.artifact.artifactId, floor };
            const artifactCard = (
              <div className="group flex min-h-16 w-full items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 text-left text-foreground transition-colors hover:border-foreground/20 hover:bg-muted/20">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
                  {presentation.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{presentation.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{presentation.meta}</span>
                </span>
                <ArrowRight
                  className="size-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                  aria-hidden
                />
              </div>
            );
            return (
              <li
                key={entry.id}
                className={cn('grid grid-cols-[4px_minmax(0,1fr)] gap-4 py-6', divider)}
                data-floor={floor}
              >
                <QuestionMarker
                  active={selectedTargetKeys.has(`artifact:${entry.artifact.artifactId}`)}
                  enabled={rejectionMode}
                  floor={floor}
                  onClick={() => onToggleTarget(target)}
                />
                <article className="min-w-0">
                  <ActorHeader
                    actor="bot"
                    createdAt={entry.artifact.createdAt}
                    botId={botId}
                    botName={resolvedBotName}
                    humanName={humanName}
                    humanAvatarUrl={humanAvatarUrl}
                  />
                  <div className="mt-3 pl-[52px]">
                    {fileArtifact ? (
                      <button
                        type="button"
                        className="block w-full border-0 bg-transparent p-0"
                        aria-label={`打开产物 ${presentation.title}`}
                        disabled={busyArtifactId === fileArtifact.artifactId}
                        onClick={() => void openFileArtifact(fileArtifact)}
                      >
                        {artifactCard}
                      </button>
                    ) : (
                      <a
                        href={presentation.href || '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="block no-underline"
                      >
                        {artifactCard}
                      </a>
                    )}
                  </div>
                </article>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="m-0 py-8 text-center text-sm text-muted-foreground">暂无互动记录。</p>
      )}

      {artifactError ? (
        <p className="m-0 mt-4 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {artifactError}
        </p>
      ) : null}

      <div className="border-t border-divider pt-5">
        {rejectionMode ? (
          <div className="space-y-3 rounded-xl border border-destructive/35 bg-destructive/5 p-4">
            <p className="m-0 text-xs text-muted-foreground">
              {floors ? `已选中问题楼层：${floors}` : '请先标记有问题的互动楼层'}
            </p>
            <Textarea
              value={rejectionMessage}
              onChange={(event) => onRejectionMessageChange(event.target.value)}
              placeholder="填写完整返工要求"
              rows={4}
              disabled={loading}
            />
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="ghost"
                className="h-8 rounded-lg px-4 text-xs"
                onClick={onCancelRejection}
                disabled={loading}
              >
                取消
              </Button>
              <Button
                variant="outline"
                className="h-8 rounded-lg px-4 text-xs"
                onClick={() => void onSubmitRejection()}
                loading={loading}
                disabled={!rejectionMessage.trim() || selectedTargets.length === 0}
              >
                提交并退回返工
              </Button>
            </div>
          </div>
        ) : enabled ? (
          <div className="space-y-2">
            <Textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="告诉 Bot 需要补充或修改的内容"
              rows={4}
              disabled={loading}
            />
            <div className="flex justify-end">
              <Button
                className="h-8 rounded-lg px-4 text-xs"
                onClick={() => void submit()}
                loading={loading}
                disabled={!message.trim()}
                leftIcon={<Send className="size-4" aria-hidden />}
              >
                发送互动
              </Button>
            </div>
          </div>
        ) : (
          <p className="m-0 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">仅待验收状态可以互动。</p>
        )}
      </div>
    </section>
  );
}
