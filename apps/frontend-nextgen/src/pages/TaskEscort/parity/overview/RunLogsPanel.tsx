import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { overviewTime } from '@/domain/taskEscortParity/overview';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortRunLogs } from '@/hooks/taskEscortParity/useTaskEscortRunDetail';
import { useState } from 'react';
import { Empty, OverviewSelect, ReadFeedback } from './OverviewShared';

export function RunLogsPanel({ context, runId }: { context: TaskEscortParityContext; runId: string }) {
  const state = useTaskEscortRunLogs(context, runId);
  const [node, setNode] = useState(''),
    [query, setQuery] = useState(''),
    [errors, setErrors] = useState(false);
  const logs = state.data ?? [],
    nodeIds = [...new Set(logs.flatMap((line) => (line.nodeId ? [line.nodeId] : [])))];
  const filtered = logs.filter(
    (line) =>
      (!node || line.nodeId === node) &&
      (!errors || ['error', 'err', 'fatal', 'critical'].includes(line.level.toLowerCase())) &&
      (!query ||
        `${line.message} ${line.source ?? ''} ${line.nodeId ?? ''}`.toLowerCase().includes(query.toLowerCase())),
  );
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <OverviewSelect
          label="日志节点"
          value={node}
          onChange={setNode}
          options={[['', '全部节点'], ...nodeIds.map((id): [string, string] => [id, id])]}
        />
        <Input
          type="search"
          aria-label="搜索日志"
          placeholder="搜索日志关键字"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="max-w-sm flex-1"
        />
        <label className="flex items-center gap-2 text-xs">
          <Checkbox checked={errors} onCheckedChange={setErrors} />
          只看错误
        </label>
        <span className="ml-auto text-xs text-muted-foreground">共 {filtered.length} 条</span>
      </div>
      <ReadFeedback state={state} label="运行日志" />
      {!state.loading &&
        !state.error &&
        (filtered.length === 0 ? (
          <Empty
            compact
            title={logs.length ? '没有匹配的日志' : '暂无实际运行日志'}
            description="仅展示 timeline 中 source=run_log 的真实日志；不生成或推断日志内容。"
          />
        ) : (
          <div className="tcov-scroll max-h-[600px] rounded-lg border border-border">
            <table className="tcov-table font-mono" aria-label="运行日志">
              <thead>
                <tr>
                  {['时间', '级别', '来源', '节点', '日志内容'].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((line) => (
                  <tr
                    key={line.id}
                    className={['error', 'err', 'fatal'].includes(line.level.toLowerCase()) ? 'text-destructive' : ''}
                  >
                    <td>{overviewTime(line.timestamp)}</td>
                    <td>{line.level.toUpperCase()}</td>
                    <td>{line.source ?? '—'}</td>
                    <td>{line.nodeId ?? '—'}</td>
                    <td className="min-w-64 whitespace-pre-wrap break-all">{line.message || '接口未提供消息正文'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}
