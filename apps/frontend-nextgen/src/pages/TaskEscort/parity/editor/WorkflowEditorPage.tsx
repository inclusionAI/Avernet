import { Button } from '@/components/ui/Button';
import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useEditorAuthoring } from '@/hooks/taskEscortParity/useEditorAuthoring';
import { useEditorAuthoringNavigation } from '@/hooks/taskEscortParity/useEditorAuthoringNavigation';
import { useEditorAuthoringPublish } from '@/hooks/taskEscortParity/useEditorAuthoringPublish';
import { useTaskEscortEditor } from '@/hooks/taskEscortParity/useTaskEscortEditor';
import { useEffect, useState } from 'react';
import { WorkflowCreationDialog } from '../WorkflowCreationDialog';
import { EditorAuthoringToolbar } from './EditorAuthoringToolbar';
import { EditorAiPanel } from './EditorAiPanel';
import { EditorLeaveDialog, EditorPublishDialog, EditorPublishReviewDialog } from './EditorAuthoringDialogs';
import { EditorHistory } from './EditorHistory';
import { EditorSettings } from './EditorInspector';
import { EditorWorkspaceBody } from './EditorWorkspaceBody';
import './editor-workspace.css';
import { EditorDialog, EmptyState, LoadingState, StateView, Tabs } from './EditorPrimitives';
import { EditorScripts } from './EditorScripts';

type Workspace = 'workflow' | 'scripts';
type Mode = 'visual' | 'split' | 'yaml';

function EditorAuthoringSession({
  context,
  original,
  canEdit,
  refresh,
}: {
  context: TaskEscortParityContext;
  original: EditorSpec;
  canEdit: boolean;
  refresh: () => void;
}) {
  const authoring = useEditorAuthoring(context, original, canEdit);
  const publication = useEditorAuthoringPublish(context, authoring.yaml, canEdit, authoring.save);
  const busy = !!authoring.phase || !!publication.phase;
  const [nodePending, setNodePending] = useState(false);
  const [settingsPending, setSettingsPending] = useState(false);
  const pendingFields = nodePending || settingsPending;
  const navigation = useEditorAuthoringNavigation(authoring.dirty || busy || pendingFields);
  const [workspace, setWorkspace] = useState<Workspace>('workflow');
  const [mode, setMode] = useState<Mode>('visual');
  const [aiOpen, setAiOpen] = useState(false);
  const [generationOpen, setGenerationOpen] = useState(false);
  const queryNodeId = context.query.nodeId;
  const [selection, setSelection] = useState<{ queryNodeId: string | undefined; id: string | null }>({
    queryNodeId,
    id: queryNodeId ?? null,
  });
  useEffect(() => setSelection({ queryNodeId, id: queryNodeId ?? null }), [queryNodeId]);
  const [settings, setSettings] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [note, setNote] = useState('');
  const spec = authoring.spec;
  const focusId = selection.queryNodeId === queryNodeId ? selection.id : queryNodeId ?? null;
  const node = spec.nodes.find((item) => item.id === focusId);
  const select = (id: string | null) => {
    if (nodePending) return;
    setSelection({ queryNodeId, id });
    context.navigate('editor', { nodeId: id ?? undefined });
  };
  return (
    <section aria-label="工作流编辑器" className="editor-authoring-workspace min-w-0 text-foreground">
      <div className="editor-workspace-tabs flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={workspace}
          onChange={(next) => { if (!pendingFields) setWorkspace(next); }}
          label="编辑器工作区"
          options={[
            { value: 'workflow', label: '工作流编排' },
            { value: 'scripts', label: '脚本文件' },
          ]}
        />
        {workspace === 'workflow' && (
          <Tabs
            value={mode}
            onChange={(next) => { if (!pendingFields) setMode(next); }}
            label="流程视图"
            options={[
              { value: 'visual', label: '画布' },
              { value: 'split', label: '对照' },
              { value: 'yaml', label: 'YAML' },
            ]}
          />
        )}
      </div>
      <EditorAuthoringToolbar authoring={authoring} canEdit={canEdit} busy={busy || pendingFields}
        onSettings={() => setSettings(true)} onHistory={() => setHistoryOpen(true)}
        onRefresh={() => navigation.requestLeave(refresh)} onGenerate={() => setGenerationOpen(true)}
        onModify={() => setAiOpen((open) => !open)} onPublish={() => setPublishOpen(true)} />
      <p
        id="editor-authoring-help"
        className="m-0 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground"
      >
        {canEdit
          ? '从左侧添加节点，连接输出与输入，再选择节点填写当前任务。修改仅保留在当前会话，保存与发布需分别确认。'
          : '当前身份只有查看权限，不能 AI 修改、编辑 YAML、保存或发布。'}{' '}
        未应用的 YAML 请先校验并应用；脚本文件使用独立的 Pack 快照，不随画布修改自动保存。
      </p>
      <div aria-live="polite">
        {authoring.persistence.loading && (
          <p role="status" className="text-xs text-muted-foreground">
            正在读取保存基线…
          </p>
        )}
        {authoring.persistence.error && (
          <div role="alert" className="text-xs text-destructive">
            {authoring.persistence.error}
            <Button size="sm" variant="outline" onClick={authoring.persistence.retry}>
              重试保存基线
            </Button>
          </div>
        )}
        {publication.error && !publication.candidate && (
          <p role="alert" className="text-xs text-destructive">
            {publication.error}
          </p>
        )}
        {publication.notice && (
          <p role="status" className="text-xs text-muted-foreground">
            {publication.notice}
          </p>
        )}
        {authoring.error && (
          <p
            role="alert"
            className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
          >
            {authoring.error}
          </p>
        )}
        {authoring.notice && (
          <p role="status" className="rounded-lg border border-input bg-muted/30 p-3 text-xs">
            {authoring.notice}
          </p>
        )}
        {busy && (
          <p role="status" className="text-xs text-muted-foreground">
            {authoring.phase === 'validate'
              ? '正在校验当前草稿…'
              : authoring.phase === 'save'
              ? '正在保存工作流…'
              : '正在保存工作流…'}
          </p>
        )}
      </div>
      {workspace === 'workflow' && (
        <div
          className={`grid min-w-0 items-start gap-4 ${aiOpen && canEdit ? '2xl:grid-cols-[minmax(0,1fr)_420px]' : ''}`}
        >
          <EditorWorkspaceBody
            authoring={authoring}
            canEdit={canEdit}
            busy={busy}
            mode={mode}
            node={node}
            focusId={focusId}
            select={select}
            nodePending={nodePending}
            onPendingChange={setNodePending}
          />
          {aiOpen && canEdit && (
            <EditorAiPanel
              context={context}
              yaml={authoring.yaml}
              spec={spec}
              blockedReason={busy || pendingFields ? '正在保存或校验，请等待操作完成。' : undefined}
              onApply={authoring.replaceDraft}
              onClose={() => setAiOpen(false)}
            />
          )}
        </div>
      )}
      {workspace === 'scripts' && <EditorScripts context={context} canEdit={canEdit} />}
      {generationOpen && <WorkflowCreationDialog context={context} initialSource="ai" onClose={() => setGenerationOpen(false)} />}
      <EditorDialog title="发布历史" open={historyOpen} onOpenChange={setHistoryOpen}>
        {historyOpen && <EditorHistory context={context} />}
      </EditorDialog>
      <EditorSettings spec={spec} open={settings} onOpenChange={setSettings}
        onChange={authoring.replaceDraft} disabled={!canEdit || busy || authoring.pendingYaml || nodePending} onPendingChange={setSettingsPending} />
      <EditorPublishDialog
        open={publishOpen}
        busy={busy}
        note={note}
        onNote={setNote}
        onClose={() => setPublishOpen(false)}
        onPublish={() => {
          setPublishOpen(false);
          void publication.prepare(note.trim() || undefined);
        }}
      />
      {publication.candidate && (
        <EditorPublishReviewDialog key={publication.candidate.review.candidateCommit} publication={publication} />
      )}
      <EditorLeaveDialog
        open={!!navigation.leave}
        onStay={navigation.cancelLeave}
        onLeave={navigation.discardAndLeave}
      />
    </section>
  );
}

function EditorSession({ context }: { context: TaskEscortParityContext }) {
  const { access, workflow } = useTaskEscortEditor(context);
  if (access.loading || access.error) return <StateView {...access} retry={access.refresh} />;
  if (access.data?.canView !== true)
    return (
      <EmptyState
        title="没有工作流查看权限"
        description="服务端未授予查看权限，未读取工作流或脚本内容。"
        action={
          <Button variant="outline" onClick={access.refresh}>
            重新检查权限
          </Button>
        }
      />
    );
  if (workflow.loading || workflow.error) return <StateView {...workflow} retry={workflow.refresh} />;
  if (!workflow.data)
    return <EmptyState title="没有可展示的工作流规格" description="未使用本地示例或默认节点替代服务端响应。" />;
  return (
    <EditorAuthoringSession
      context={context}
      original={workflow.data}
      canEdit={access.data.canEdit && !!context.userId}
      refresh={() => {
        access.refresh();
        workflow.refresh();
      }}
    />
  );
}

/** Keying the whole authoring session prevents old identity/workflow results from painting. */
export function WorkflowEditorPage({ context }: { context: TaskEscortParityContext }) {
  if (context.loading) return <LoadingState />;
  if (context.error) return <StateView loading={false} error={context.error} retry={context.refreshWorkflows} />;
  if (!context.ready || !context.workflowId)
    return <EmptyState title="请先选择工作流" description="编辑器仅展示已选择工作流的真实规格。" />;
  return (
    <EditorSession key={JSON.stringify([context.scopeKey, context.userId, context.workflowId])} context={context} />
  );
}
