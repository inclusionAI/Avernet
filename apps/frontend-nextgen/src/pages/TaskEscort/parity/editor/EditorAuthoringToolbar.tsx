import { Button } from '@/components/ui/Button';
import type { EditorAuthoring } from '@/hooks/taskEscortParity/useEditorAuthoring';
import { Download, History, RefreshCw, Settings2, Sparkles } from 'lucide-react';

export function EditorAuthoringToolbar({ authoring, canEdit, busy, onSettings, onHistory, onRefresh,
  onGenerate, onModify, onPublish }: {
  authoring: EditorAuthoring;
  canEdit: boolean;
  busy: boolean;
  onSettings: () => void;
  onHistory: () => void;
  onRefresh: () => void;
  onGenerate: () => void;
  onModify: () => void;
  onPublish: () => void;
}) {
  const spec = authoring.spec;
  const exportYaml = () => {
    const url = URL.createObjectURL(new Blob([authoring.yaml], { type: 'text/yaml;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${spec.id.replace(/[^A-Za-z0-9_.-]/g, '_')}.yaml`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return <header className="editor-authoring-toolbar flex flex-wrap items-center justify-between gap-3">
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
      <span className="break-all font-mono">{spec.id}{spec.version === undefined ? '' : ` v${spec.version}`}</span>
      <span className="text-muted-foreground">· {spec.title}</span>
      <span className={`rounded-full px-2 py-1 text-xs ${authoring.dirty ? 'bg-muted text-foreground' : 'bg-primary/10 text-primary'}`}>
        {authoring.dirty ? '未保存' : '已读取保存态'}
      </span>
      {!canEdit && <span className="text-xs text-muted-foreground">只读权限</span>}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" onClick={onSettings}><Settings2 size={14} aria-hidden />流程设置</Button>
      <Button variant="outline" size="sm" disabled={busy} onClick={onRefresh} aria-label="刷新规格"><RefreshCw size={14} aria-hidden /></Button>
      <Button variant="outline" size="sm" disabled={!canEdit || busy} onClick={onGenerate}><Sparkles size={14} aria-hidden />AI 生成</Button>
      <Button variant="outline" size="sm" disabled={!canEdit || busy} onClick={onModify} aria-describedby="editor-authoring-help">AI 修改</Button>
      <Button variant="outline" size="sm" onClick={onHistory}><History size={14} aria-hidden />发布历史</Button>
      <Button variant="outline" size="sm" onClick={exportYaml} aria-label="导出工作流 YAML 备份"><Download size={14} aria-hidden /></Button>
      <Button size="sm" disabled={!canEdit || busy || !authoring.saveReady} onClick={() => void authoring.save()} aria-describedby="editor-authoring-help">
        {authoring.phase === 'save' ? '保存中…' : '保存工作流'}
      </Button>
      <Button variant="outline" size="sm" className="border-success/40 text-success" disabled={!canEdit || busy || !authoring.saveReady}
        onClick={onPublish} aria-describedby="editor-authoring-help">发布工作流</Button>
    </div>
  </header>;
}
