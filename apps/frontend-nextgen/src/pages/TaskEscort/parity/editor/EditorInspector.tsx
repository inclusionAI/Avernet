import { Button } from '@/components/ui/Button';
import type { EditorNode, EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { EditorCode, EditorDialog, EditorFields, EmptyState } from './EditorPrimitives';

export function EditorInspector({ node, close }: { node: EditorNode | undefined; close: () => void }) {
  return (
    <aside aria-label="节点属性" className="min-w-0 space-y-4 rounded-lg border border-border bg-card p-4">
      <header className="flex items-center justify-between gap-2">
        <h3 className="m-0 text-sm font-semibold">节点属性</h3>
        <Button variant="ghost" size="sm" onClick={close} disabled={!node}>
          关闭节点面板
        </Button>
      </header>
      {!node ? (
        <EmptyState title="选择节点查看配置" description="点击画布中的真实节点，查看执行器、依赖和完整配置。" compact />
      ) : (
        <>
          <EditorFields value={{ id: node.id, ...(node.title !== undefined ? { title: node.title } : {}) }} />
          <section className="space-y-3">
            <h4 className="m-0 text-xs font-semibold">执行器</h4>
            <EditorFields value={node.executor} />
          </section>
          {node.dependsOn && (
            <section>
              <h4 className="text-xs font-semibold">依赖节点</h4>
              <p className="break-all font-mono text-xs">
                {node.dependsOn.length ? node.dependsOn.join(' → ') : '显式空依赖'}
              </p>
            </section>
          )}
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-xs font-medium">高级配置</summary>
            <div className="mt-3">
              <EditorFields
                value={Object.fromEntries(
                  Object.entries(node).filter(([key]) => !['id', 'title', 'executor', 'dependsOn'].includes(key)),
                )}
              />
            </div>
            <p className="text-xs text-muted-foreground">仅展示已配置字段；执行器超时保留原字段与原单位。</p>
          </details>
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-xs font-medium">完整节点 JSON</summary>
            <div className="mt-3">
              <EditorCode label="完整节点 JSON" text={JSON.stringify(node, null, 2)} />
            </div>
          </details>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled>
              复制节点
            </Button>
            <Button variant="outline" size="sm" disabled>
              删除节点
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">节点修改未接入，仅查看，不会改变工作流。</p>
        </>
      )}
    </aside>
  );
}
export function EditorSettings({
  spec,
  open,
  onOpenChange,
}: {
  spec: EditorSpec;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const settings = Object.fromEntries(Object.entries(spec).filter(([key]) => key !== 'nodes'));
  return (
    <EditorDialog title="流程设置" open={open} onOpenChange={onOpenChange}>
      <EditorFields value={settings} />
      <details className="rounded-lg border border-border p-3">
        <summary className="cursor-pointer text-xs font-medium">完整流程配置（不含节点）</summary>
        <EditorCode label="流程配置 JSON" text={JSON.stringify(settings, null, 2)} />
      </details>
      <div className="space-y-2">
        <Button disabled>保存设置</Button>
        <p className="text-xs text-muted-foreground">设置写入未接入，未添加任何默认参数、身份或输出。</p>
      </div>
    </EditorDialog>
  );
}
