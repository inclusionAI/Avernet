import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Spin } from '@/components/ui/Spin';
import { useDetailFocusRestore } from '@/hooks/taskEscortWorkspace/useDetailFocus';
import { useTaskEscortLiveRunDetail } from '@/hooks/taskEscortWorkspace/useTaskEscortLiveRunDetail';
import type { TaskEscortWorkspaceState } from '@/hooks/useTaskEscortWorkspace';

export function LiveRunDetail({ demo }: { demo: TaskEscortWorkspaceState }) {
  const focusRestore = useDetailFocusRestore();
  const detail = useTaskEscortLiveRunDetail(demo.selectedRun, demo.loadRunDetail);
  return (
    <Modal
      open={demo.selectedRun !== null}
      onOpenChange={(open) => {
        if (!open) demo.setSelectedRun(null);
      }}
    >
      <ModalContent {...focusRestore} size="lg" className="task-escort-workspace">
        <ModalHeader>
          <ModalTitle>{detail.run?.name ?? '运行详情'}</ModalTitle>
          <ModalDescription>仅展示后端实际返回的运行与节点执行记录，不推断拓扑或诊断结论。</ModalDescription>
        </ModalHeader>
        {detail.loading && <Spin tip="正在读取运行详情" />}
        {detail.error && (
          <Empty
            title="运行详情读取失败"
            description="记录可能已不可见或服务暂时不可用。"
            action={
              <Button variant="outline" onClick={detail.retry}>
                重试详情
              </Button>
            }
          />
        )}
        {detail.run && (
          <>
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-4 text-xs">
              <Badge tone={detail.run.tone}>{detail.run.statusLabel}</Badge>
              <span className="break-all font-mono">{detail.run.id}</span>
              <span className="text-muted-foreground">耗时 {detail.run.durationLabel}</span>
              <span className="text-muted-foreground">{detail.run.startedAtLabel}</span>
            </div>
            {!detail.nodes.length ? (
              <Empty compact title="暂无节点执行记录" description="后端未返回节点，无法展示节点轨迹。" />
            ) : (
              <div className="space-y-3">
                {detail.nodes.map((node) => (
                  <section className="rounded-lg border border-border p-4" key={node.id}>
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <h2 className="min-w-0 break-all font-medium">{node.title}</h2>
                      <Badge tone={node.tone}>{node.statusLabel}</Badge>
                      <span className="text-muted-foreground">{node.durationLabel}</span>
                    </div>
                    {node.error && (
                      <p className="mt-3 whitespace-pre-wrap break-all text-xs text-destructive">{node.error}</p>
                    )}
                    {(node.input || node.output) && (
                      <details className="mt-3 text-xs">
                        <summary className="cursor-pointer text-primary">查看实际输入与输出</summary>
                        <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3">{`输入\n${
                          node.input ?? '—'
                        }\n\n输出\n${node.output ?? '—'}`}</pre>
                      </details>
                    )}
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </ModalContent>
    </Modal>
  );
}
