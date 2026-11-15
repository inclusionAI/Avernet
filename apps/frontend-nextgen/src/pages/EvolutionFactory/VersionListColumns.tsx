import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { DataTableColumn } from '@/components/ui/DataTable';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { formatTime, type EvolutionVersion } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { Hint } from './sharedPrimitives';
import { Comparison, VersionActions, versionLabel, versionStatus } from './VersionActions';

/** 整页表格与 Bot 管理列表面板同一几何规范（72px 行 + sticky 操作列 + 44px 表头）。 */
export const COLUMN_WIDTHS = [
  'w-[11%]', 'w-[14%]', 'w-[14%]', 'w-[9%]', 'w-[10%]', 'w-[16%]', 'w-[13%]', 'w-[13%]',
];
const taskLinkClass =
  'h-7 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';

/** 版本状态 → 语义徽标：已接受（已登记）绿、未接受红、晋升失败红、Pack 缺位黄、其余中性灰。 */
function versionTone(label: string) {
  if (label === '晋升失败') return 'error' as const;
  if (label === '未接受') return 'error' as const;
  if (label.includes('未登记')) return 'warning' as const;
  if (label === '已接受') return 'success' as const;
  return 'neutral' as const;
}

/** 进化版本列表列定义。viewDiff 每渲染重建（依赖 refs 与 setState），故不做 memo，直接随渲染生成。 */
export function buildVersionColumns(
  viewDiff: (v: EvolutionVersion) => void,
  botNameOf: (v: EvolutionVersion) => string,
): DataTableColumn<EvolutionVersion>[] {
  return [
    {
      id: 'version',
      header: '版本',
      width: COLUMN_WIDTHS[0],
      cell: (v) => (
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-foreground">{versionLabel(v)}</p>
          <Hint content={v.pack?.packId}>
            <p className="mt-0.5 truncate text-xs text-content-icon">{v.pack?.packId || 'Pack 未登记'}</p>
          </Hint>
        </div>
      ),
    },
    {
      id: 'bot',
      header: 'Bot',
      width: COLUMN_WIDTHS[1],
      className: 'text-[13px]',
      cell: (v) => (
        <div className="flex min-w-0 items-center gap-3">
          <BotDisplayAvatar botId={v.botId} name={botNameOf(v)} size={32} />
          <div className="min-w-0">
            <Hint content={botNameOf(v)}>
              <p className="truncate font-medium text-foreground">{botNameOf(v)}</p>
            </Hint>
            <Hint content={v.botId}>
              <p className="mt-0.5 truncate text-xs text-content-icon">{v.botId}</p>
            </Hint>
          </div>
        </div>
      ),
    },
    {
      id: 'sourceTask',
      header: '来源任务',
      width: COLUMN_WIDTHS[2],
      cell: (v) => {
        const name = typeof v.taskName === 'string' && v.taskName.trim() ? v.taskName : v.taskId;
        return v.taskId ? (
          <div className="min-w-0">
            <Hint content={name}>
              <Button
                type="button"
                variant="ghost"
                className={`${taskLinkClass} max-w-full justify-start px-0`}
                aria-label={`查看来源任务 ${name}`}
                onClick={() => history.push(`/evolution-factory/tasks/${encodeURIComponent(v.taskId)}`)}
              >
                <span className="truncate">{name}</span>
              </Button>
            </Hint>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        );
      },
    },
    {
      id: 'review',
      header: '状态',
      width: COLUMN_WIDTHS[3],
      cell: (v) => {
        const label = versionStatus(v);
        return (
          <Badge tone={versionTone(label)}>{label}</Badge>
        );
      },
    },
    {
      id: 'reviewSpec',
      header: 'Review / Spec',
      width: COLUMN_WIDTHS[4],
      cell: (v) => {
        const value = `${v.reviewStatus || '—'} / ${v.specVersion || '—'}`;
        return (
          <Hint content={value}>
            <span className="block truncate text-xs text-muted-foreground">{value}</span>
          </Hint>
        );
      },
    },
    {
      id: 'bench',
      header: 'Test Bench',
      width: COLUMN_WIDTHS[5],
      cell: (v) => <Comparison version={v} />,
    },
    {
      id: 'createdAt',
      header: '生成时间',
      width: COLUMN_WIDTHS[6],
      cell: (v) => <span className="whitespace-nowrap text-xs text-muted-foreground">{formatTime(v.createdAt)}</span>,
    },
    {
      id: 'actions',
      header: '操作',
      width: COLUMN_WIDTHS[7],
      align: 'start',
      sticky: 'right',
      cell: (v) => (
        <div onClick={(e) => e.stopPropagation()}>
          <VersionActions version={v} onDiff={(v) => viewDiff(v)} />
        </div>
      ),
    },
  ];
}
