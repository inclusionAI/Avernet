// 平台指标大盘 Drawer。
// Internal overlay（getMetricsDashboard().value.url 非空）：iframe 嵌入 AntMonitor 监控大盘，
//   复刻旧 ocb 指标大盘（MetricsDashboardDrawer.tsx）：Spin 加载骨架 + title 旁外链图标 + sandbox iframe，关闭后重置 loading。
// Open Core 回退（url 为 null）：渲染静态占位 4 区（3 条成功率折线占位 + Arca-quota 三项），数据待指标接口接入。
// 复用项目 <Drawer>（side=right size=lg=640px，对齐旧版 min(88vw,680px) 与 PRD width:640），禁 antd/裸 button；加载态用白名单 <Spin>。
import { getCapabilities } from '@/capabilities';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle, Spin } from '@/components/ui';
import { ExternalLink } from 'lucide-react';
import { useCallback, useState } from 'react';

interface PlatformMetricsPanelProps {
  open: boolean;
  onClose: () => void;
}

interface MetricRow {
  title: string;
  dataKey: string;
  color: string;
  avg: string;
}

interface QuotaItem {
  label: string;
  value: string;
  color: string;
}

const METRIC_ROWS: MetricRow[] = [
  { title: '服务端接口调用成功率', dataKey: 'server', color: 'rgb(22,93,255)', avg: '99.72' },
  { title: 'Theta 模型调用成功率', dataKey: 'theta', color: 'rgb(114,46,209)', avg: '99.35' },
  { title: '服务 Bot - OpenAP 执行', dataKey: 'openap', color: 'rgb(245,154,35)', avg: '98.91' },
];

const QUOTA_ITEMS: QuotaItem[] = [
  { label: '运行容器数量', value: '8,299', color: 'rgb(22,93,255)' },
  { label: '租户剩余 CPU 预算', value: '508 核', color: 'rgb(0,180,42)' },
  { label: '租户剩余内存预算', value: '1,016 GB', color: 'rgb(245,154,35)' },
];

export function PlatformMetricsPanel({ open, onClose }: PlatformMetricsPanelProps) {
  // URL 非空 → iframe 嵌入 AntMonitor 大盘；为 null → Open Core 回退静态占位 4 区。
  const url = getCapabilities().getMetricsDashboard().value.url;
  const [iframeLoading, setIframeLoading] = useState(true);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (next) return;
      onClose();
      // 关闭后重置 loading，下次打开仍展示骨架（对齐旧 ocb MetricsDashboardDrawer）
      requestAnimationFrame(() => setIframeLoading(true));
    },
    [onClose],
  );

  const handleIframeLoad = useCallback(() => setIframeLoading(false), []);

  return (
    <Drawer open={open} onOpenChange={handleOpenChange}>
      <DrawerContent side="right" size="lg" bodyClassName={url ? 'p-0 flex flex-col' : undefined}>
        {url ? (
          <>
            <DrawerHeader className="px-6 pt-6">
              <DrawerTitle className="flex items-center gap-2 text-base font-semibold">
                指标大盘
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  // 阻止冒泡触发抽屉关闭/标题点击行为
                  onClick={(e) => e.stopPropagation()}
                  className="inline-flex items-center text-muted-foreground transition-colors hover:text-foreground"
                  aria-label="在新窗口打开 AntMonitor 大盘"
                >
                  <ExternalLink className="size-3.5" />
                </a>
              </DrawerTitle>
            </DrawerHeader>
            <div className="relative min-h-0 w-full flex-1">
              {iframeLoading && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-background">
                  <Spin tip="加载指标大盘中..." />
                </div>
              )}
              <iframe
                src={url}
                className="h-full w-full border-0"
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                title="AntMonitor 指标大盘"
                onLoad={handleIframeLoad}
              />
            </div>
          </>
        ) : (
          <>
            <DrawerHeader>
              <DrawerTitle className="text-base font-semibold">平台指标大盘</DrawerTitle>
            </DrawerHeader>
            <div className="flex flex-col gap-5">
              {METRIC_ROWS.map((row) => (
                <div key={row.dataKey} className="rounded-lg border border-border bg-background p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <span className="flex items-center gap-2 text-sm font-medium">
                      <span className="h-2 w-2 rounded-full" style={{ background: row.color }} />
                      {row.title}
                    </span>
                    <span className="text-xs text-muted-foreground">平均 {row.avg}%</span>
                  </div>
                  {/* 占位折线区：真实图表随指标 spec 落地（避免本期引入图表库体积） */}
                  <div className="flex h-24 items-center justify-center rounded-md bg-muted/40 text-xs text-muted-foreground">
                    图表占位（数据待接入）
                  </div>
                </div>
              ))}

              {/* Arca-quota 使用 */}
              <div className="rounded-lg border border-border bg-background p-4">
                <div className="mb-4 text-sm font-medium">Arca-quota 使用</div>
                <div className="grid grid-cols-3 gap-3">
                  {QUOTA_ITEMS.map((q) => (
                    <div key={q.label} className="rounded-md bg-muted/40 p-3 text-center">
                      <div className="text-lg font-semibold" style={{ color: q.color }}>
                        {q.value}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">{q.label}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
      </DrawerContent>
    </Drawer>
  );
}

export default PlatformMetricsPanel;
