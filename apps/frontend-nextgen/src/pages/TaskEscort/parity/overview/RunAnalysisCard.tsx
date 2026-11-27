import { Badge } from '@/components/ui/Badge';
import { Button } from './OverviewShared';

export function RunAnalysisCard({ status, onEvidence }: { status: string | null; onEvidence: () => void }) {
  return (
    <section aria-label="运行分析" className="tcov-card space-y-3 p-4">
      <h3 className="text-sm font-semibold">运行分析</h3>
      <section aria-label="单次运行分析" className="rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-medium">单次运行分析</h4>
          <Badge tone="neutral">{status ?? '状态不可用'}</Badge>
        </div>
        <p className="my-2 text-xs text-muted-foreground">
          {status
            ? `服务端单次运行分析状态：${status}；实际结果以该运行档案中的证据为准。`
            : '接口未提供单次运行分析状态；不代表已完成分析或不存在档案证据。'}
        </p>
        <Button variant="outline" size="sm" onClick={onEvidence}>
          查看单次分析与档案
        </Button>
      </section>
      <section aria-label="聚合分析" className="rounded-lg border border-border bg-muted/20 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-medium">聚合分析（独立状态）</h4>
          <Badge tone="neutral">不可用</Badge>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          未接入已验证的聚合分析协议；不从单次运行状态推断跨运行结论、分析完成或修复成功。
        </p>
        <Button disabled variant="outline" size="sm" className="mt-3">
          查看聚合结论与来源
        </Button>
      </section>
    </section>
  );
}
