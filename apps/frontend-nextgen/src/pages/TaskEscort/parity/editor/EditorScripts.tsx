import { Button } from '@/components/ui/Button';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEditorScripts } from '@/hooks/taskEscortParity/useTaskEscortEditor';
import { useState } from 'react';
import { EditorCode, EditorFields, EmptyState, StateView } from './EditorPrimitives';

export function EditorScripts({ context, canEdit }: { context: TaskEscortParityContext; canEdit: boolean }) {
  const resource = useTaskEscortEditorScripts(context, canEdit);
  const [selected, setSelected] = useState<string | null>(null);
  if (!canEdit)
    return (
      <EmptyState title="无法查看脚本" description="脚本读取需要工作流编辑权限和 Pack 级读取授权；当前没有编辑权限。" />
    );
  if (resource.loading || resource.error) return <StateView {...resource} retry={resource.refresh} />;
  const pack = resource.data;
  if (!pack) return null;
  const file = pack.files.find((item) => item.path === selected) ?? (selected === null ? pack.files[0] : undefined);
  return (
    <section aria-label="Pack 脚本文件" className="space-y-4">
      <p role="status" className="m-0 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
        脚本来自已授权的真实 Pack 快照，仅供读取。完整 Pack 保存与精确版本发布未接入，不能编辑或执行脚本。
      </p>
      {pack.files.length === 0 ? (
        <EmptyState title="快照没有脚本文件" description="当前 Pack 返回的 files 为空，未补入示例文件。" />
      ) : (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[220px_minmax(0,1fr)_240px]">
          <nav aria-label="脚本文件列表" className="min-w-0 space-y-2 rounded-lg border border-border bg-card p-3">
            <h3 className="text-xs font-semibold">脚本文件 · {pack.files.length}</h3>
            {pack.files.map((item) => (
              <Button
                key={item.path}
                variant="ghost"
                size="sm"
                className="h-auto min-h-8 w-full justify-start whitespace-normal break-all text-left font-mono"
                aria-pressed={file?.path === item.path}
                onClick={() => setSelected(item.path)}
              >
                {item.path}
              </Button>
            ))}
          </nav>
          <section aria-label="脚本查看器" className="min-w-0 space-y-3">
            <h3 className="break-all font-mono text-xs font-semibold">{file?.path ?? '请选择文件'}</h3>
            {file && <EditorCode label="脚本内容" text={file.content} />}
          </section>
          <aside aria-label="脚本信息" className="min-w-0 space-y-4 rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-semibold">快照信息</h3>
            <EditorFields
              value={{
                workflowId: pack.workflowId,
                packId: pack.packId,
                baseRef: pack.baseRef,
                baseCommit: pack.baseCommit,
                ...(file ? { path: file.path, mode: file.mode } : {}),
              }}
            />
            <Button variant="outline" disabled>
              保存脚本
            </Button>
            <p className="text-xs text-muted-foreground">脚本写入未接入。</p>
          </aside>
        </div>
      )}
      <Button variant="outline" size="sm" onClick={resource.refresh}>
        重新读取脚本快照
      </Button>
    </section>
  );
}
