import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoAcceptanceTarget, TodoArtifact, TodoEvent } from '@/domain/todos/models';
import { MessageSquarePlus, Send } from 'lucide-react';
import { useMemo, useState } from 'react';
import { TodoInteractionStream } from './TodoInteractionStream';
import {
  buildInteractionEntries,
  indexEntriesByTarget,
  selectedFloors,
  targetKey,
} from './todoInteractionModel';

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
    () => buildInteractionEntries(interactions, events, artifacts, resolvedBotName),
    [artifacts, events, interactions, resolvedBotName],
  );
  const selectedTargetKeys = useMemo(
    () => new Set(selectedTargets.map(targetKey)),
    [selectedTargets],
  );
  const entryByTarget = useMemo(() => indexEntriesByTarget(entries), [entries]);
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

      <TodoInteractionStream
        entries={entries}
        entryByTarget={entryByTarget}
        selectedTargetKeys={selectedTargetKeys}
        rejectionMode={rejectionMode}
        botId={botId}
        botName={resolvedBotName}
        humanName={humanName}
        humanAvatarUrl={humanAvatarUrl}
        busyArtifactId={busyArtifactId}
        onToggleTarget={onToggleTarget}
        onOpenFile={(artifact) => void openFileArtifact(artifact)}
      />

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
