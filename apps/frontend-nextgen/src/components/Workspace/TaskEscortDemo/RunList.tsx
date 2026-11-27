import { Button, Empty, Input } from '@/components/ui';
import { Search } from 'lucide-react';
import { DemoSelect, StatusBadge } from './DemoPrimitives';
import { DemoNode, DemoRun, RunStatus, STATUS_LABEL } from './model';

export type StatusFilter = RunStatus | 'all';
export function RunList({
  runs,
  nodes,
  status,
  onStatusChange,
  keyword,
  onKeywordChange,
  onFocusRun,
}: {
  runs: DemoRun[];
  nodes: DemoNode[];
  status: StatusFilter;
  onStatusChange: (status: StatusFilter) => void;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  onFocusRun: (id: string) => void;
}) {
  const filtered = runs.filter((run) => {
    const node = nodes.find((item) => item.id === run.node);
    const haystack = `${run.id} ${node?.name} ${run.version}`.toLowerCase();
    return (status === 'all' || run.status === status) && haystack.includes(keyword.trim().toLowerCase());
  });
  const clear = () => {
    onStatusChange('all');
    onKeywordChange('');
  };
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-3.5 py-3">
        <DemoSelect<StatusFilter>
          label="运行状态筛选"
          value={status}
          onChange={onStatusChange}
          options={[
            { value: 'all', label: '全部状态' },
            ...Object.entries(STATUS_LABEL).map(([value, label]) => ({ value: value as RunStatus, label })),
          ]}
        />
        <div className="relative min-w-40 flex-1">
          <Search aria-hidden className="pointer-events-none absolute left-2.5 top-2.5 size-3 text-muted-foreground" />
          <Input
            aria-label="运行关键词"
            placeholder="搜索 RUN ID、节点或版本"
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            className="pl-8"
          />
        </div>
        {(status !== 'all' || keyword) && (
          <Button size="sm" variant="ghost" onClick={clear}>
            清除筛选
          </Button>
        )}
        <span aria-live="polite" className="text-[10px] text-muted-foreground">
          {filtered.length} 条演示记录
        </span>
      </div>
      {filtered.length === 0 ? (
        <Empty
          compact
          title={runs.length === 0 ? '暂无运行记录' : '没有匹配的演示记录'}
          description={
            runs.length === 0 ? '当前工作流在所选范围内暂无演示记录。' : '尝试更换状态或关键词，概览指标不受影响。'
          }
          action={
            status !== 'all' || keyword ? (
              <Button variant="outline" size="sm" onClick={clear}>
                重置筛选
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="overflow-x-auto">
          <table aria-label="模拟运行记录" className="w-full min-w-[660px] text-left text-[11px]">
            <thead className="border-y border-border bg-muted/40 text-[10px] text-muted-foreground">
              <tr>
                {['RUN ID', '状态', '节点', '版本', '开始时间', '耗时', '查看'].map((label) => (
                  <th key={label} scope="col" className="whitespace-nowrap px-3 py-2 font-normal first:pl-3.5">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((run) => (
                <tr
                  key={run.id}
                  className="border-b border-border/50 transition-colors last:border-0 hover:bg-muted/30"
                >
                  <td className="px-3.5 py-2.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onFocusRun(run.id)}
                      className="h-6 px-1.5 font-mono text-[11px] text-primary"
                    >
                      {run.id}
                    </Button>
                  </td>
                  <td className="px-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="whitespace-nowrap px-3">
                    {nodes.find((node) => node.id === run.node)?.name ?? run.node}
                  </td>
                  <td className="px-3 font-mono text-[10px] text-muted-foreground">{run.version}</td>
                  <td className="whitespace-nowrap px-3 font-mono text-[10px] text-muted-foreground">
                    {run.startedAt}
                  </td>
                  <td className="whitespace-nowrap px-3 font-mono">{run.duration}</td>
                  <td className="px-3">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`查看运行 ${run.id}`}
                      onClick={() => onFocusRun(run.id)}
                      className="h-6 px-1.5 text-[10px] text-primary"
                    >
                      查看
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="px-3.5 py-2 text-[10px] text-muted-foreground">仅展示内置 DEMO 记录，不读取真实运行历史。</p>
    </div>
  );
}
