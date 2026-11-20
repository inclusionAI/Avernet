import { Button, Empty, Input, Segmented, Textarea } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Code2, RotateCcw, Save, Search, Workflow } from 'lucide-react';
import { useState } from 'react';
import { getNodeIcon, NodeCanvas } from './NodeCanvas';
import { NodeProperties } from './NodeProperties';
import { TaskEscortDemoState } from './useTaskEscortDemo';

export function WorkflowEditor({ demo }: { demo: TaskEscortDemoState }) {
  const [search, setSearch] = useState('');
  const { workflow } = demo;
  const selected = demo.nodes.find((node) => node.id === demo.selectedId) ?? demo.nodes[0];
  const library = demo.nodes.filter((node) =>
    `${node.name} ${node.id}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  if (!workflow) return null;
  return (
    <section aria-label="演示工作流编辑器" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">工作流编辑器</h2>
          <p className="mt-1 text-[10px] text-muted-foreground">
            示意编排 · {demo.nodes.length} 节点精简示例，并非线上完整工作流复刻
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={demo.reset}>
            <RotateCcw className="size-3.5" aria-hidden />
            重置演示
          </Button>
          <Button size="sm" onClick={demo.save} disabled={!!demo.error}>
            <Save className="size-3.5" aria-hidden />
            保存本地草稿
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented
          aria-label="编排编辑方式"
          value={demo.view}
          onChange={demo.setView}
          options={[
            { value: 'canvas', label: '可视化', icon: <Workflow className="size-3.5" aria-hidden /> },
            { value: 'yaml', label: 'YAML', icon: <Code2 className="size-3.5" aria-hidden /> },
          ]}
          className="w-48"
        />
        <span className={cn('text-[10px]', demo.dirty ? 'text-primary' : 'text-muted-foreground')}>
          {demo.dirty ? '有未保存的本地修改' : '草稿已同步'}
        </span>
      </div>
      <p className="rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-[10px] leading-5 text-muted-foreground">
        仅保存在当前页面内存；切换工作流或关闭后重新打开可继续编辑，刷新或卸载组件后清空。不会运行、部署或发送任何数据。
      </p>
      {demo.notice && (
        <p role="status" className="rounded-lg bg-success/10 px-3 py-2 text-[11px] text-foreground">
          {demo.notice}
        </p>
      )}
      {demo.error && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-[11px] leading-5 text-destructive"
        >
          <p>{demo.error}</p>
          <p>无效 YAML 不会写入画布或保存；画布保留最近有效内容，修改属性将替换当前 YAML。</p>
        </div>
      )}
      {demo.view === 'yaml' ? (
        <div className="rounded-xl border border-border bg-card p-3.5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <label htmlFor="task-escort-demo-yaml" className="text-xs font-medium">
              工作流 YAML · DEMO
            </label>
            <span className="text-[10px] text-muted-foreground">可编辑 name / description / timeoutSeconds</span>
          </div>
          <Textarea
            id="task-escort-demo-yaml"
            aria-invalid={!!demo.error}
            aria-describedby="task-escort-demo-yaml-help"
            spellCheck={false}
            value={demo.yaml}
            onChange={(event) => demo.updateYaml(event.target.value)}
            className="min-h-[490px] resize-y bg-muted/20 font-mono text-[11px] leading-6"
          />
          <p id="task-escort-demo-yaml-help" className="mt-2 text-[10px] text-muted-foreground">
            输入有效 YAML 即同步到可视化属性；保留当前工作流标识、demo: true 和 {workflow.nodes.length}{' '}
            个节点的顺序、id、kind。
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto pb-2">
          <div className="escort-editor-columns grid min-w-[790px] grid-cols-[140px_minmax(340px,1fr)_230px] items-start gap-3">
            <aside aria-label="示例节点库" className="overflow-hidden rounded-xl border border-border bg-card">
              <div className="border-b border-border p-3">
                <h3 className="text-xs font-medium">
                  节点库 <span className="ml-1 text-[10px] font-normal text-muted-foreground">{demo.nodes.length}</span>
                </h3>
                <div className="relative mt-3">
                  <Search
                    className="pointer-events-none absolute left-2 top-2.5 size-3 text-muted-foreground"
                    aria-hidden
                  />
                  <Input
                    aria-label="搜索示例节点"
                    placeholder="搜索节点"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    className="pl-6 pr-1"
                  />
                </div>
              </div>
              <div className="escort-node-library-items space-y-1 p-2">
                {library.map((node) => {
                  const Icon = getNodeIcon(node.kind);
                  return (
                    <Button
                      key={node.id}
                      variant="ghost"
                      size="sm"
                      onClick={() => demo.setSelectedId(node.id)}
                      aria-pressed={node.id === selected?.id}
                      aria-label={`从节点库选择 ${node.name}`}
                      className={cn(
                        'h-auto min-h-10 w-full justify-start px-2 py-2 text-[11px]',
                        node.id === selected?.id && 'bg-primary/5 text-primary',
                      )}
                    >
                      <Icon className="size-3.5 shrink-0" aria-hidden />
                      <span className="truncate">{node.name || '未命名节点'}</span>
                    </Button>
                  );
                })}
                {library.length === 0 && <Empty compact title="未找到节点" className="px-1 [&_p]:text-[10px]" />}
              </div>
              <p className="border-t border-border p-3 text-[9px] leading-4 text-muted-foreground">
                点击定位已有节点。演示不支持添加或删除节点。
              </p>
            </aside>
            <NodeCanvas
              nodes={demo.nodes}
              nodeStatuses={workflow.nodeStatuses}
              selectedId={demo.selectedId}
              onSelect={demo.setSelectedId}
            />
            {selected ? (
              <NodeProperties node={selected} nodeStatuses={workflow.nodeStatuses} onChange={demo.updateNode} />
            ) : (
              <Empty compact title="暂无可编辑节点" />
            )}
          </div>
        </div>
      )}
    </section>
  );
}
