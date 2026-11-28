import { Badge } from './primitives';
import { Button } from './primitives';
import { callbackCompensationReason } from '../../models/callbackCompensationErrors';
import type { CallbackCompensationEligibility } from '../../models/callbackCompensationTypes';
import type { CallbackCompensationState } from '../../hooks/callback-compensation/useCallbackCompensation';
import { useState } from 'react';
import { CallbackCompensationTime } from './CallbackCompensationScope';

const labels: Record<CallbackCompensationEligibility, string> = {
  eligible: '可补偿',
  delivered: '已投递',
  review: '需人工核实',
  ineligible: '不符合条件',
};
export function CallbackCompensationPreview({ state }: { state: CallbackCompensationState }) {
  const [filter, setFilter] = useState<CallbackCompensationEligibility | 'all'>('all');
  const preview = state.preview!;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-medium">补偿预览</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            仅可勾选服务端判定为 eligible 的运行；未确认幂等不会获得投递资格。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={state.modifyRange}>
          修改范围
        </Button>
      </div>
      {state.previewRange?.from && state.previewRange.to ? (
        <CallbackCompensationTime from={state.previewRange.from} to={state.previewRange.to} />
      ) : (
        <p className="rounded-lg bg-muted p-3 text-xs text-muted-foreground">
          运行 ID 范围：{state.previewRange?.flowIds?.length ?? 0} 个指定运行（不按时间范围筛选）
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {(Object.keys(labels) as CallbackCompensationEligibility[]).map((key) => (
          <div
            key={key}
            className={`flex items-center gap-3 rounded-lg border p-4 ${
              key === 'eligible' ? 'border-primary/20 bg-primary/5' : 'border-border bg-card'
            }`}
          >
            <strong className={`text-2xl tabular-nums ${key === 'eligible' ? 'text-primary' : 'text-foreground'}`}>
              {preview.summary[key]}
            </strong>
            <span className="text-xs text-muted-foreground">{labels[key]}</span>
          </div>
        ))}
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-3">
          <div className="flex flex-wrap gap-1" aria-label="预览状态筛选">
            {(['all', ...Object.keys(labels)] as const).map((value) => (
              <Button
                key={value}
                size="sm"
                variant={filter === value ? 'default' : 'ghost'}
                aria-pressed={filter === value}
                onClick={() => setFilter(value as typeof filter)}
              >
                {value === 'all' ? '全部' : labels[value as CallbackCompensationEligibility]}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">已选择 {state.selected.length} 项</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[650px] text-left text-xs">
            <thead className="bg-muted text-muted-foreground">
              <tr>
                <th className="p-3">
                  <span className="sr-only">选择</span>
                </th>
                <th className="p-3 font-medium">运行 ID</th>
                <th className="p-3 font-medium">资格</th>
                <th className="p-3 font-medium">服务端原因 / 证据</th>
              </tr>
            </thead>
            <tbody>
              {preview.items
                .filter((item) => filter === 'all' || item.status === filter)
                .map((item) => (
                  <tr
                    key={item.itemId}
                    className={`border-t border-border ${state.selected.includes(item.itemId) ? 'bg-primary/5' : ''}`}
                  >
                    <td className="p-3 align-top">
                      <input
                        type="checkbox"
                        aria-label={`选择 ${item.flowId}`}
                        className="accent-primary"
                        checked={state.selected.includes(item.itemId)}
                        disabled={item.status !== 'eligible' || !state.executionReady || state.expired || !!state.busy}
                        onChange={(event) => state.select(item.itemId, event.target.checked)}
                      />
                    </td>
                    <td className="max-w-64 break-all p-3 align-top font-mono">{item.flowId}</td>
                    <td className="p-3 align-top">
                      <Badge
                        tone={item.status === 'eligible' ? 'primary' : item.status === 'review' ? 'warning' : 'neutral'}
                      >
                        {labels[item.status]}
                      </Badge>
                    </td>
                    <td className="max-w-lg p-3 align-top">
                      <ul className="space-y-1 leading-5 text-muted-foreground">
                        {item.reasons.map((reason, index) => (
                          <li key={index}>{callbackCompensationReason(reason)}</li>
                        ))}
                      </ul>
                      {item.payload !== null && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-primary">查看脱敏 payload</summary>
                          <p className="my-2 text-[11px] text-muted-foreground">
                            仅展示服务端脱敏内容，不构造可投递请求。
                          </p>
                          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-3 font-mono text-[11px]">
                            {JSON.stringify(item.payload, null, 2)}
                          </pre>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {!preview.items.length && <p className="p-10 text-center text-xs text-muted-foreground">所选范围没有预览项</p>}
      </div>
      <p className="text-xs text-muted-foreground">
        预览有效期至 {new Date(preview.expiresAt).toLocaleString()}；已投递、需核实或不符合条件的项不可补发。
      </p>
      {state.expired && (
        <p role="alert" className="text-xs text-destructive">
          预览已过期，请修改范围后重新预览；已有批次和结果未知项不会重发。
        </p>
      )}
      <div className="flex justify-end">
        <Button
          disabled={!state.executionReady || state.expired || !state.selected.length || !!state.busy}
          onClick={state.confirm}
        >
          下一步：确认补偿
        </Button>
      </div>
    </div>
  );
}
