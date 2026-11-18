import { Button } from '@/components/ui/Button';
import { editorYaml } from '@/domain/taskEscortParity/editor';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEditor } from '@/hooks/taskEscortParity/useTaskEscortEditor';
import { useEffect, useMemo, useState } from 'react';
import { EditorGraph } from './EditorGraph';
import { EditorHistory } from './EditorHistory';
import { EditorInspector, EditorSettings } from './EditorInspector';
import { EditorCode, EditorDialog, EmptyState, LoadingState, StateView, Tabs } from './EditorPrimitives';
import { EditorScripts } from './EditorScripts';

type Workspace = 'workflow' | 'scripts';
type Mode = 'visual' | 'split' | 'yaml';

function EditorSession({ context }: { context: TaskEscortParityContext }) {
  const { access, workflow } = useTaskEscortEditor(context);
  const [workspace, setWorkspace] = useState<Workspace>('workflow');
  const [mode, setMode] = useState<Mode>('visual');
  const queryNodeId = context.query.nodeId;
  const [selection, setSelection] = useState<{ queryNodeId: string | undefined; id: string | null }>({
    queryNodeId,
    id: queryNodeId ?? null,
  });
  useEffect(() => setSelection({ queryNodeId, id: queryNodeId ?? null }), [queryNodeId]);
  const [settings, setSettings] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const spec = workflow.data;
  const yaml = useMemo(() => (spec ? editorYaml(spec) : ''), [spec]);
  // Query updates (back/forward or another view) remain authoritative when provided.
  const focusId = selection.queryNodeId === queryNodeId ? selection.id : queryNodeId ?? null;
  const node = spec?.nodes.find((item) => item.id === focusId);
  const select = (id: string | null) => {
    setSelection({ queryNodeId, id });
    context.navigate('editor', { nodeId: id ?? undefined });
  };
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
  if (!spec)
    return <EmptyState title="没有可展示的工作流规格" description="未使用本地示例或默认节点替代服务端响应。" />;
  return (
    <section aria-label="工作流编辑器" className="min-w-0 space-y-4 text-foreground">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="m-0 break-words text-lg font-semibold">{spec.title}</h2>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
            {spec.id} · {spec.version === undefined ? '未提供版本' : `v${spec.version}`} ·{' '}
            {access.data.canEdit ? '有编辑权限 · 本轮只读' : '只读权限'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setSettings(true)}>
            流程设置
          </Button>
          <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)}>
            发布历史
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              access.refresh();
              workflow.refresh();
            }}
          >
            刷新规格
          </Button>
          <Button variant="outline" size="sm" disabled aria-describedby="editor-readonly-reason">
            保存工作流
          </Button>
          <Button size="sm" disabled aria-describedby="editor-readonly-reason">
            发布工作流
          </Button>
        </div>
      </header>
      <p
        id="editor-readonly-reason"
        role="status"
        className="m-0 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground"
      >
        本轮未接入保存、发布和节点写入；所有内容只读，不会模拟成功。YAML 不可编辑，因此没有未保存草稿。
      </p>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={workspace}
          onChange={setWorkspace}
          label="编辑器工作区"
          options={[
            { value: 'workflow', label: '工作流编排' },
            { value: 'scripts', label: '脚本文件' },
          ]}
        />
        {workspace === 'workflow' && (
          <Tabs
            value={mode}
            onChange={setMode}
            label="流程视图"
            options={[
              { value: 'visual', label: '画布' },
              { value: 'split', label: '对照' },
              { value: 'yaml', label: 'YAML' },
            ]}
          />
        )}
      </div>
      {workspace === 'workflow' && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="m-0 text-xs text-muted-foreground">
              选择节点查看完整属性；对照模式同时显示画布和保存态 YAML。
            </p>
            <Button variant="outline" size="sm" disabled>
              添加节点
            </Button>
          </div>
          {focusId && !node && (
            <p role="status" className="rounded-lg border border-border p-3 text-xs text-muted-foreground">
              所选节点 {focusId} 不在当前规格中，请重新选择。
            </p>
          )}
          <div className={`grid min-w-0 gap-4 ${mode !== 'yaml' ? 'xl:grid-cols-[minmax(0,1fr)_320px]' : ''}`}>
            <div className={`grid min-w-0 gap-4 ${mode === 'split' ? 'xl:grid-cols-2' : ''}`}>
              {mode !== 'yaml' && <EditorGraph spec={spec} selectedId={node?.id ?? null} select={select} />}
              {mode !== 'visual' && (
                <section aria-label="工作流规格 YAML" className="min-w-0 space-y-2">
                  <h3 className="m-0 text-xs font-semibold">保存态 YAML · 只读</h3>
                  <EditorCode label="工作流 YAML" text={yaml} />
                </section>
              )}
            </div>
            {mode !== 'yaml' && <EditorInspector node={node} close={() => select(null)} />}
          </div>
        </>
      )}
      {workspace === 'scripts' && <EditorScripts context={context} canEdit={access.data.canEdit} />}
      <EditorDialog title="发布历史" open={historyOpen} onOpenChange={setHistoryOpen}>
        {historyOpen && <EditorHistory context={context} />}
      </EditorDialog>
      <EditorSettings spec={spec} open={settings} onOpenChange={setSettings} />
    </section>
  );
}

/** A keyed session clears local focus before a new identity/workflow can paint old evidence. */
export function WorkflowEditorPage({ context }: { context: TaskEscortParityContext }) {
  if (context.loading) return <LoadingState />;
  if (context.error) return <StateView loading={false} error={context.error} retry={context.refreshWorkflows} />;
  if (!context.ready || !context.workflowId)
    return <EmptyState title="请先选择工作流" description="编辑器仅展示已选择工作流的真实规格。" />;
  return (
    <EditorSession key={JSON.stringify([context.scopeKey, context.userId, context.workflowId])} context={context} />
  );
}
