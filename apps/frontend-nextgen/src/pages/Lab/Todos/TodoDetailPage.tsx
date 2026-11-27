import { Badge, Button, Card, Empty, Spin } from '@/components/ui';
import type { TodoAcceptanceTarget } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL, canInteractWithTodo } from '@/domain/todos/status';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useTodoDetail } from '@/hooks/useTodoDetail';
import { cn } from '@/utils/cn';
import { history, useLocation, useParams } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { TodoAcceptanceActions } from './components/TodoAcceptanceActions';
import { TodoArtifactList } from './components/TodoArtifactList';
import { TodoDetailItem, TodoDetailTab } from './components/TodoDetailPrimitives';
import { TodoInteractionPanel } from './components/TodoInteractionPanel';
import { TodoProductInteractionComposer, TodoProductInteractionPanel } from './components/TodoProductInteractionPanel';
import { TodoProgressPanel } from './components/TodoProgressPanel';

type DetailView = 'progress' | 'interactions' | 'artifacts';

function createDecisionId(): string {
  const randomUuid = globalThis.crypto?.randomUUID?.();
  if (randomUuid) return `decision_${randomUuid.replaceAll('-', '')}`;
  return `decision_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
}

function formatDateTime(value?: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

/** 独立待办详情页：围绕阶段进度、互动和产物呈现执行过程。 */
export default function TodoDetailPage() {
  const { todoId } = useParams<{ todoId: string }>();
  const location = useLocation();
  const currentSpace = useSpaceContext((state) => state.currentSpace);
  const { identity: humanIdentity } = useHumanIdentity();
  const [view, setView] = useState<DetailView>('progress');
  const [rejectionMode, setRejectionMode] = useState(false);
  const [rejectionMessage, setRejectionMessage] = useState('');
  const [selectedTargets, setSelectedTargets] = useState<TodoAcceptanceTarget[]>([]);
  const [rejectionDecisionId, setRejectionDecisionId] = useState<string | null>(null);
  const approvalDecisionId = useRef<string | null>(null);
  const detail = useTodoDetail(todoId || null);
  const todo = detail.selectedTodo;

  useEffect(() => {
    setRejectionMode(false);
    setRejectionMessage('');
    setSelectedTargets([]);
    setRejectionDecisionId(null);
    approvalDecisionId.current = null;
  }, [todoId]);

  useEffect(() => {
    const focus = new URLSearchParams(location.search).get('focus');
    if (focus === 'interaction') setView('interactions');
  }, [location.search]);

  const beginRejection = () => {
    setRejectionMode(true);
    setRejectionDecisionId((current) => current || createDecisionId());
    setView('interactions');
  };
  const cancelRejection = () => {
    setRejectionMode(false);
    setRejectionMessage('');
    setSelectedTargets([]);
    setRejectionDecisionId(null);
  };
  const toggleRejectionTarget = (target: TodoAcceptanceTarget) => {
    setSelectedTargets((current) => {
      const existing = current.findIndex((item) => item.kind === target.kind && item.id === target.id);
      if (existing >= 0) return current.filter((_, index) => index !== existing);
      return [...current, target];
    });
  };
  const readProgressArtifact = async (
    artifactId: string,
    disposition: 'inline' | 'attachment'
  ): Promise<Blob> => {
    const blob = await detail.readArtifactContent(artifactId, disposition);
    if (disposition === 'attachment') {
      const artifact = detail.artifacts.find((item) => item.artifactId === artifactId);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download =
        artifact?.kind === 'file'
          ? artifact.content.fileName || artifact.artifactName
          : artifact?.artifactName || artifactId;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    }
    return blob;
  };

  const submitRejection = async () => {
    const decisionId = rejectionDecisionId || createDecisionId();
    if (!rejectionDecisionId) setRejectionDecisionId(decisionId);
    const success = await detail.accept({
      decisionId,
      verdict: 'rejected',
      message: rejectionMessage,
      targets: selectedTargets,
    });
    if (success) cancelRejection();
  };
  const approve = async () => {
    const decisionId = approvalDecisionId.current || createDecisionId();
    approvalDecisionId.current = decisionId;
    const success = await detail.accept({ decisionId, verdict: 'approved', targets: [] });
    if (success) approvalDecisionId.current = null;
  };

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto bg-background">
      {detail.loading && !todo ? (
        <Spin tip="加载待办详情中…" className="py-24" />
      ) : detail.error && !todo ? (
        <div className="mx-auto w-full max-w-6xl px-5 py-16">
          <Empty
            title="待办详情加载失败"
            description={detail.error}
            action={
              <Button variant="secondary" onClick={() => void detail.refresh()}>
                重试
              </Button>
            }
          />
        </div>
      ) : todo ? (
        <>
          <header className="border-b border-divider bg-background px-4 pt-6 pb-4 sm:px-6 lg:px-10">
            <div className="mx-auto flex w-full max-w-[1200px] items-center gap-3">
              <Button
                variant="ghost"
                size="icon"
                aria-label="返回待办列表"
                className="size-8 rounded-lg bg-muted/70 hover:bg-muted"
                onClick={() => history.push('/lab/todos')}
              >
                <ArrowLeft className="size-4" aria-hidden />
              </Button>
              <h1 className="m-0 min-w-0 truncate text-[18px] font-semibold tracking-tight text-foreground">
                {todo.title}
              </h1>
              <Badge className={cn('shrink-0 border-0 px-2 py-0.5 text-xs', TODO_STATUS_CLASS[todo.status])}>
                {TODO_STATUS_LABEL[todo.status]}
              </Badge>
            </div>
          </header>

          <div className="mx-auto flex w-full max-w-[1200px] flex-col items-start gap-6 px-4 py-6 sm:px-6 lg:flex-row lg:px-10">
            <div className="min-w-0 flex-1 space-y-5">
              <section aria-label="待办概要">
                <p className="m-0 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                  {todo.description || '暂无描述'}
                </p>
              </section>

              <Card className="overflow-hidden rounded-xl border-border shadow-none">
                <div
                  role="group"
                  aria-label="待办详情视图"
                  className="flex items-center border-b border-divider px-5"
                >
                  <TodoDetailTab active={view === 'progress'} onClick={() => setView('progress')}>
                    进度
                  </TodoDetailTab>
                  <TodoDetailTab active={view === 'interactions'} onClick={() => setView('interactions')}>
                    互动
                  </TodoDetailTab>
                  <TodoDetailTab active={view === 'artifacts'} onClick={() => setView('artifacts')}>
                    产物 {detail.artifacts.length > 0 ? detail.artifacts.length : ''}
                  </TodoDetailTab>
                </div>

                <div className="px-5 py-4">
                  {view === 'progress' ? (
                    <div className="space-y-5">
                      <TodoProgressPanel
                        progress={detail.progress}
                        artifacts={detail.artifacts}
                        loading={detail.progressLoading}
                        error={detail.progressError}
                        onRetry={() => void detail.refreshProgress()}
                        onExpandStage={(stageCode) => void detail.refreshProgress(stageCode)}
                        onReadArtifact={readProgressArtifact}
                      />
                      {todo.status === 'pending_action' ? (
                        <TodoAcceptanceActions
                          loading={detail.mutationLoading}
                          rejecting={rejectionMode}
                          targets={selectedTargets}
                          onBeginReject={beginRejection}
                          onContinueReject={() => setView('interactions')}
                          onCancelReject={cancelRejection}
                          onApprove={approve}
                        />
                      ) : null}
                    </div>
                  ) : null}

                  {view === 'interactions' && detail.isProductInteractionRuntime ? (
                    <TodoProductInteractionPanel
                      todoId={todoId || ''}
                      interactions={detail.productInteractionPage?.items ?? []}
                      canSend={detail.productInteractionPage?.canSend ?? false}
                      blockingInteractionId={detail.productInteractionPage?.blockingInteractionId}
                      todoStatus={todo.status}
                      loading={detail.productInteractionLoading || detail.mutationLoading}
                      error={detail.productInteractionError}
                      humanName={humanIdentity?.displayName || '我'}
                      botName={todo.templateId ? todo.templateName || todo.templateId : todo.botName || todo.botId}
                      onCancel={detail.productStopInteraction}
                      onRetry={() => void detail.refreshProductInteractions()}
                      onSubmit={detail.productInteract}
                    />
                  ) : null}

                  {view === 'interactions' && !detail.isProductInteractionRuntime ? (
                    <TodoInteractionPanel
                      interactions={detail.interactions}
                      events={detail.events}
                      artifacts={detail.artifacts}
                      botId={todo.botId}
                      botName={todo.templateId ? todo.templateName || todo.templateId : todo.botName || todo.botId}
                      humanName={humanIdentity?.displayName || '我'}
                      humanAvatarUrl={humanIdentity?.avatarUrl}
                      enabled={canInteractWithTodo(todo.status)}
                      loading={detail.mutationLoading}
                      mentionBots={detail.mentionBots}
                      mentionBotsLoading={detail.mentionBotsLoading}
                      mentionBotsError={detail.mentionBotsError}
                      rejectionMode={rejectionMode}
                      selectedTargets={selectedTargets}
                      rejectionMessage={rejectionMessage}
                      onToggleTarget={toggleRejectionTarget}
                      onRejectionMessageChange={setRejectionMessage}
                      onCancelRejection={cancelRejection}
                      onSubmitRejection={submitRejection}
                      onReadArtifact={detail.readArtifactContent}
                      onSubmit={detail.interact}
                    />
                  ) : null}

                  {view === 'artifacts' ? (
                    <TodoArtifactList
                      artifacts={detail.artifacts}
                      loading={detail.artifactsLoading}
                      error={detail.artifactsError}
                      onRetry={detail.refreshArtifacts}
                      onReadFile={detail.readArtifactContent}
                    />
                  ) : null}
                </div>
              </Card>

              {view === 'interactions' && detail.isProductInteractionRuntime ? (
                <TodoProductInteractionComposer
                  todoId={todoId || ''}
                  canSend={detail.productInteractionPage?.canSend ?? false}
                  todoStatus={todo.status}
                  loading={detail.productInteractionLoading || detail.mutationLoading}
                  onSubmit={detail.productInteract}
                />
              ) : null}

              {detail.error ? (
                <p className="m-0 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {detail.error}
                </p>
              ) : null}
            </div>

            <aside className="w-full shrink-0 lg:w-64" aria-label="待办详情">
              <div className="sticky top-6 rounded-xl border border-border bg-card p-4">
                <h2 className="m-0 mb-3 text-sm font-semibold text-foreground">详情</h2>
                <div className="space-y-2.5">
                  <TodoDetailItem
                    label="状态"
                    value={
                      <Badge className={cn('border-0 px-1.5 py-0.5 text-[10px]', TODO_STATUS_CLASS[todo.status])}>
                        {TODO_STATUS_LABEL[todo.status]}
                      </Badge>
                    }
                  />
                  <TodoDetailItem
                    label="执行方"
                    value={todo.templateId ? todo.templateName || todo.templateId : todo.botName || todo.botId}
                  />
                  <TodoDetailItem label="负责人" value={todo.ownerName || todo.ownerId || '—'} />
                  <TodoDetailItem
                    label="当前空间"
                    value={currentSpace?.spaceName || `空间 ${todo.spaceId}`}
                  />
                  <TodoDetailItem label="创建时间" value={formatDateTime(todo.createdAt)} />
                  <TodoDetailItem label="更新时间" value={formatDateTime(todo.updatedAt)} />
                </div>
              </div>
            </aside>
          </div>
        </>
      ) : null}
    </main>
  );
}
