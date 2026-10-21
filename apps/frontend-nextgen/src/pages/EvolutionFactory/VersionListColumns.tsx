import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { DataTableColumn } from '@/components/ui/DataTable';
import { formatTime, type EvolutionVersion } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { Comparison, VersionActions, versionLabel, versionStatus } from './VersionActions';

/** 整页表格与 Bot 管理列表面板同一几何规范（h-18 行 + sticky 操作列 + 44px 表头）。 */
export const COLUMN_WIDTHS = [
  'w-[90px]',
  'w-[170px]',
  'w-[clamp(180px,15vw,240px)]',
  'w-[130px]',
  'w-[150px]',
  'w-[140px]',
  'w-[130px]',
  'w-[160px]',
  'w-[200px]',
];
const taskLinkClass = 'h-7 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';

/** 版本状态 → 语义徽标：已接受（已登记）绿、未接受/晋升失败红、Pack 缺位黄、其余中性灰。 */
function versionTone(label: string) {
  if (label === '晋升失败' || label === '未接受') return 'error' as const;
  if (label.includes('未登记')) return 'warning' as const;
  if (label === '已接受') return 'success' as const;
  return 'neutral' as const;
}

/** 进化版本列表列定义。viewDiff 每渲染重建（依赖 refs 与 setState），故不做 memo，直接随渲染生成。 */
export function buildVersionColumns(viewDiff: (v: EvolutionVersion) => void): DataTableColumn<EvolutionVersion>[] {
  return [
    {
      id: 'version',
      header: '版本',
      width: COLUMN_WIDTHS[0],
      cell: (v) => <span className="text-[13px] font-medium text-foreground">{versionLabel(v)}</span>,
    },
    {
      id: 'bot',
      header: 'Bot / Owner',
      width: COLUMN_WIDTHS[1],
      className: 'break-all text-[13px]',
      // botId 保持为 td 的直接文本节点（getNodeText 断言依赖），Owner 退为下一行小字。
      cell: (v) => (
        <>
          {v.botId}
          <p className="mt-0.5 text-xs text-content-icon">{v.userId}</p>
        </>
      ),
    },
    {
      id: 'task',
      header: '来源任务',
      width: COLUMN_WIDTHS[2],
      cell: (v) => (
        <Button
          type="button"
          variant="ghost"
          style={{ height: 'auto', whiteSpace: 'normal' }}
          className={taskLinkClass}
          onClick={() => history.push(`/evolution-factory/tasks/${encodeURIComponent(v.taskId)}`)}
        >
          <span className="break-all text-left">{String(v.taskName || v.taskId)}</span>
        </Button>
      ),
    },
    {
      id: 'status',
      header: '状态',
      width: COLUMN_WIDTHS[3],
      cell: (v) => {
        const label = versionStatus(v);
        return <Badge tone={versionTone(label)}>{label}</Badge>;
      },
    },
    {
      id: 'bench',
      header: 'Test Bench',
      width: COLUMN_WIDTHS[4],
      cell: (v) => <Comparison version={v} />,
    },
    {
      id: 'review',
      header: 'Review / Spec',
      width: COLUMN_WIDTHS[5],
      cell: (v) => (
        <span className="text-xs text-muted-foreground">
          {v.reviewStatus || '—'} / {v.specVersion || '—'}
        </span>
      ),
    },
    {
      id: 'pack',
      header: 'Pack',
      width: COLUMN_WIDTHS[6],
      cell: (v) => <span className="break-all text-xs text-muted-foreground">{v.pack?.packId || '未登记'}</span>,
    },
    {
      id: 'time',
      header: '时间',
      width: COLUMN_WIDTHS[7],
      cell: (v) => <span className="text-xs text-muted-foreground">{formatTime(v.createdAt)}</span>,
    },
    {
      id: 'actions',
      header: '操作',
      width: COLUMN_WIDTHS[8],
      align: 'end',
      className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
      headerClassName: 'sticky right-0 z-10 bg-muted/50',
      cell: (v) => (
        <div onClick={(e) => e.stopPropagation()}>
          <VersionActions version={v} onDiff={(v) => viewDiff(v)} />
        </div>
      ),
    },
  ];
}