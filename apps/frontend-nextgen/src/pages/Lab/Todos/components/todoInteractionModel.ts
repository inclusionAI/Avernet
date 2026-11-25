import type { TodoAcceptanceTarget, TodoArtifact, TodoEvent } from '@/domain/todos/models';

export type InteractionEntry =
  | { kind: 'message'; id: string; event: TodoEvent }
  | { kind: 'artifact'; id: string; artifact: TodoArtifact; event?: TodoEvent }
  | { kind: 'notice'; id: string; content: string; createdAt: string; noticeType: 'started' | 'completed' };

export type TargetEntry = Exclude<InteractionEntry, { kind: 'notice' }>;

export interface InteractionReplyReference {
  eventId: string;
  floor: number;
  label: string;
  content: string;
  targetExecutionId?: string;
  targetStageInstanceId?: string;
  replyable: boolean;
  replyDisabledReason?: string;
}

export interface ArtifactPresentation {
  icon: 'file' | 'link' | 'code';
  title: string;
  meta: string;
  details: string[];
  href: string | null;
}

export function formatInteractionTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function eventKey(item: TodoEvent): string {
  return item.eventId || `${item.executionId}-${item.eventSeq}`;
}

function payloadText(item: TodoEvent, key: 'content' | 'objective'): string {
  const value = item.payload[key];
  return typeof value === 'string' ? value : '';
}

function payloadId(item: TodoEvent, key: string): string {
  const value = item.payload[key];
  return typeof value === 'string' ? value : '';
}

export function replyToEventId(item: TodoEvent): string | undefined {
  return payloadId(item, 'reply_to_event_id') || undefined;
}

export function interactionContent(item: TodoEvent): string {
  if (item.content) return item.content;
  const content = payloadText(item, 'content');
  if (content) return content;
  if (item.eventType === 'task.created') {
    const objective = payloadText(item, 'objective');
    if (objective) return objective;
  }
  if (item.eventType === 'task.completed') return '本轮执行已完成，但未上报文本结果。';
  return '该互动未包含文本内容。';
}

export function rejectionTargets(item: TodoEvent): TodoAcceptanceTarget[] {
  if (item.eventType !== 'task.rejected' || !Array.isArray(item.payload.targets)) return [];
  return item.payload.targets
    .map((target) => {
      if (!target || typeof target !== 'object') return null;
      const value = target as Record<string, unknown>;
      const { kind, id, floor } = value;
      if (
        (kind !== 'event' && kind !== 'artifact') ||
        typeof id !== 'string' ||
        !id ||
        typeof floor !== 'number' ||
        !Number.isInteger(floor) ||
        floor < 1
      ) {
        return null;
      }
      return { kind, id, floor };
    })
    .filter((target): target is TodoAcceptanceTarget => target !== null)
    .sort((left, right) => left.floor - right.floor);
}

export function selectedFloors(targets: TodoAcceptanceTarget[]): string {
  return [...targets]
    .sort((left, right) => left.floor - right.floor)
    .map((target) => `f${target.floor}`)
    .join('、');
}

export function buildInteractionEntries(
  interactions: TodoEvent[],
  events: TodoEvent[],
  artifacts: TodoArtifact[],
  botName: string,
): InteractionEntry[] {
  const enrichedInteractions = new Map(interactions.map((item) => [eventKey(item), item]));
  const stream = new Map<string, TodoEvent>();
  events.forEach((item) => stream.set(eventKey(item), enrichedInteractions.get(eventKey(item)) ?? item));
  interactions.forEach((item) => stream.set(eventKey(item), item));

  const artifactById = new Map(artifacts.map((artifact) => [artifact.artifactId, artifact]));
  const renderedArtifactIds = new Set<string>();
  const entries: InteractionEntry[] = [];

  [...stream.values()].forEach((item) => {
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
    if (item.eventType === 'artifact.reported') {
      const artifactId = payloadId(item, 'artifact_id');
      const artifact = artifactById.get(artifactId);
      if (artifact && !renderedArtifactIds.has(artifact.artifactId)) {
        renderedArtifactIds.add(artifact.artifactId);
        entries.push({ kind: 'artifact', id: `artifact-${artifact.artifactId}`, artifact, event: item });
      } else if (item.content) {
        entries.push({ kind: 'message', id: `message-${key}`, event: item });
      }
      return;
    }
    if (item.eventType !== 'task.completed') return;

    entries.push({
      kind: 'notice',
      id: `notice-completed-${key}`,
      content: '转为待处理，等待发起人验收',
      createdAt: item.createdAt,
      noticeType: 'completed',
    });
  });

  artifacts
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

export function artifactPresentation(artifact: Exclude<TodoArtifact, { kind: 'text' }>): ArtifactPresentation {
  if (artifact.kind === 'file') {
    const extension = artifact.content.fileName.split('.').pop()?.toLowerCase() || '文件';
    return {
      icon: 'file',
      title: artifact.content.fileName || artifact.artifactName,
      meta: `${extension} · ${formatFileSize(artifact.content.size)}`,
      details: [],
      href: null,
    };
  }
  if (artifact.kind === 'url_link') {
    return {
      icon: 'link',
      title: artifact.artifactName,
      meta: artifact.content.type || '外部链接',
      details: [artifact.content.url],
      href: artifact.content.url,
    };
  }
  return {
    icon: 'code',
    title: artifact.content.codeBaseName || artifact.artifactName,
    meta: artifact.content.prLink ? 'Pull Request' : '代码产物',
    details: [artifact.content.codeBaseAddress, artifact.content.prLink, artifact.content.prDesc].filter(
      (value): value is string => Boolean(value),
    ),
    href: artifact.content.prLink || artifact.content.codeBaseAddress,
  };
}

export function targetKey(target: Pick<TodoAcceptanceTarget, 'kind' | 'id'>): string {
  return `${target.kind}:${target.id}`;
}

export function indexEntriesByTarget(entries: InteractionEntry[]): Map<string, TargetEntry> {
  const indexed = new Map<string, TargetEntry>();
  entries.forEach((entry) => {
    if (entry.kind === 'message') indexed.set(`event:${entry.event.eventId}`, entry);
    if (entry.kind === 'artifact') indexed.set(`artifact:${entry.artifact.artifactId}`, entry);
  });
  return indexed;
}

function previewText(value: string, maxLength = 60): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  const characters = Array.from(normalized);
  return characters.length > maxLength ? `${characters.slice(0, maxLength).join('')}…` : normalized;
}

function replyContent(entry: TargetEntry): string {
  if (entry.kind === 'message') return previewText(interactionContent(entry.event)) || '该互动未包含文本内容。';
  if (entry.artifact.kind === 'text') return previewText(entry.artifact.content.text) || entry.artifact.artifactName;
  const presentation = artifactPresentation(entry.artifact);
  return previewText(`${presentation.title} · ${presentation.meta}`);
}

export function indexReplyReferences(
  entries: InteractionEntry[],
  botName: string,
  humanName: string,
): Map<string, InteractionReplyReference> {
  const indexed = new Map<string, InteractionReplyReference>();
  entries.forEach((entry, index) => {
    if (entry.kind === 'notice') return;
    const eventId = entry.kind === 'message' ? entry.event.eventId : entry.event?.eventId;
    if (!eventId) return;
    indexed.set(eventId, {
      eventId,
      floor: index + 1,
      label: entry.kind === 'artifact' ? '产物' : entry.event.actorType === 'bot' ? botName : humanName,
      content: replyContent(entry),
      targetExecutionId: entry.kind === 'message' ? entry.event.todoExecutionId : entry.event?.todoExecutionId,
      targetStageInstanceId: entry.kind === 'message' ? entry.event.stageInstanceId : entry.event?.stageInstanceId,
      replyable: entry.kind === 'message' ? entry.event.replyable : entry.event?.replyable === true,
      replyDisabledReason:
        entry.kind === 'message' ? entry.event.replyDisabledReason : entry.event?.replyDisabledReason,
    });
  });
  return indexed;
}

export function replyDisabledLabel(reason?: string): string {
  if (reason === 'superseded') return '历史版本不可回复';
  if (reason === 'event_not_replyable') return '该事件不可回复';
  if (reason === 'todo_not_replyable') return '当前待办不可回复';
  return '当前不可回复';
}

export function rejectionQuote(
  target: TodoAcceptanceTarget,
  entryByTarget: Map<string, TargetEntry>,
  botName: string,
  humanName: string,
): { label: string; content: string } {
  const referenced = entryByTarget.get(targetKey(target));
  if (!referenced) {
    return { label: target.kind === 'artifact' ? '产物' : '互动', content: '原内容暂不可用' };
  }
  if (referenced.kind === 'artifact') {
    if (referenced.artifact.kind === 'text') {
      return { label: '文本产物', content: previewText(referenced.artifact.content.text) };
    }
    const presentation = artifactPresentation(referenced.artifact);
    return { label: '产物', content: previewText(`${presentation.title} · ${presentation.meta}`) };
  }
  return {
    label: referenced.event.actorType === 'bot' ? botName : humanName,
    content: previewText(interactionContent(referenced.event)) || '该互动未包含文本内容。',
  };
}
