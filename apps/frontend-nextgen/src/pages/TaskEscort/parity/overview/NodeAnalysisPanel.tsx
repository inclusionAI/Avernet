import { Input } from '@/components/ui/Input';
import { overviewDuration } from '@/domain/taskEscortParity/overview';
import type { OverviewNodeStats, OverviewReadState } from '@/domain/taskEscortParity/overviewTypes';
import { useMemo, useState } from 'react';
import { Button, Empty, EvidenceValue, OverviewSelect, ReadFeedback } from './OverviewShared';

function NodeRow({
  node,
  expanded,
  onToggle,
}: {
  node: OverviewNodeStats['nodes'][number];
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr>
        <td>
          <Button variant="link" size="sm" className="px-0" aria-expanded={expanded} onClick={onToggle}>
            {node.nodeId}
          </Button>
          {node.title && <p className="text-[11px] text-muted-foreground">{node.title}</p>}
        </td>
        <td>{node.executor || '—'}</td>
        <td>{node.totalExecutions ?? '—'}</td>
        <td
          className={
            node.failureRate !== null && node.failureRate !== undefined && node.failureRate > 0.1
              ? 'text-destructive'
              : ''
          }
        >
          {node.failureRate === null || node.failureRate === undefined
            ? '—'
            : `${(node.failureRate * 100).toFixed(1)}%`}{' '}
          ({node.failedCount ?? '—'})
        </td>
        <td>{node.avgRetryCount?.toFixed(1) ?? '—'}</td>
        <td>{overviewDuration(node.p50DurationMs)}</td>
        <td>{overviewDuration(node.p95DurationMs)}</td>
        <td>{node.avgTokens?.toLocaleString() ?? '—'}</td>
        <td className="max-w-64 whitespace-normal">
          {node.errorCategories.map((error) => `${error.category}(${error.count ?? '—'})`).join(', ') || '—'}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9}>
            <div className="space-y-3 py-2">
              <EvidenceValue label="错误分类详情" value={node.errorCategories.length ? node.errorCategories : null} />
              <EvidenceValue label="最近错误" value={node.lastError} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function NodeAnalysisPanel({
  state,
  filter,
  onFilter,
}: {
  state: OverviewReadState<OverviewNodeStats>;
  filter: string;
  onFilter: (id: string) => void;
}) {
  const [sort, setSort] = useState('failureRate'),
    [expanded, setExpanded] = useState<string | null>(null);
  const nodes = useMemo(
    () =>
      [...(state.data?.nodes ?? [])]
        .filter((node) => `${node.nodeId} ${node.title ?? ''}`.toLowerCase().includes(filter.toLowerCase()))
        .sort(
          (a, b) =>
            (sort === 'p95'
              ? b.p95DurationMs ?? -1
              : sort === 'count'
              ? b.totalExecutions ?? -1
              : b.failureRate ?? -1) -
            (sort === 'p95' ? a.p95DurationMs ?? -1 : sort === 'count' ? a.totalExecutions ?? -1 : a.failureRate ?? -1),
        ),
    [state.data, filter, sort],
  );
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="search"
          aria-label="筛选节点"
          placeholder="筛选节点 ID / 名称"
          value={filter}
          onChange={(event) => onFilter(event.target.value)}
          className="max-w-sm"
        />
        <OverviewSelect
          label="节点排序"
          value={sort}
          onChange={setSort}
          options={[
            ['failureRate', '失败率'],
            ['p95', '耗时'],
            ['count', '执行次数'],
          ]}
        />
        {filter && (
          <Button variant="ghost" size="sm" onClick={() => onFilter('')}>
            清除节点筛选
          </Button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {state.data?.totalRuns ?? '—'} 次运行 · {nodes.length} 节点
        </span>
      </div>
      <p className="text-[11px] text-muted-foreground">节点分析使用接口全部历史口径，不受运行列表筛选影响。</p>
      <ReadFeedback state={state} label="节点统计" />
      {!state.loading &&
        !state.error &&
        (nodes.length === 0 ? (
          <Empty compact title="暂无匹配的节点统计数据" />
        ) : (
          <div className="tcov-scroll">
            <table className="tcov-table" aria-label="节点分析">
              <thead>
                <tr>
                  {['节点', '执行器', '执行', '失败率', '重试', 'P50', 'P95 耗时', 'Token', '错误分类'].map((name) => (
                    <th key={name}>{name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => (
                  <NodeRow
                    key={node.nodeId}
                    node={node}
                    expanded={expanded === node.nodeId}
                    onToggle={() => setExpanded(expanded === node.nodeId ? null : node.nodeId)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}
