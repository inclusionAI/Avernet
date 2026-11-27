import type { OverviewDetailedRun, OverviewLaunchField } from '@/domain/taskEscortParity/overviewTypes';
import { useId, useState } from 'react';
import { Button } from './OverviewShared';

function LaunchValue({ field }: { field: OverviewLaunchField }) {
  const contentId = useId();
  const [expanded, setExpanded] = useState(false);
  const expandable = field.text !== null && (field.text.length > 600 || field.text.split('\n').length > 5);
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-xs font-medium">{field.label}</h4>
          <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">{field.source}</p>
        </div>
        {expandable && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-1 text-primary"
            aria-label={`${expanded ? '收起' : '展开'}${field.label}`}
            aria-expanded={expanded}
            aria-controls={contentId}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? '收起全文' : '展开全文'}
          </Button>
        )}
      </div>
      {field.text === null ? (
        <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
          接口未提供{field.label}
        </p>
      ) : (
        <pre
          id={contentId}
          role="region"
          aria-label={`${field.label}内容`}
          tabIndex={0}
          className={`run-launch-value ${expanded ? 'run-launch-expanded' : ''}`}
        >
          {field.text}
        </pre>
      )}
      {field.note && <p className="text-[11px] text-muted-foreground">{field.note}</p>}
    </div>
  );
}

export function RunLaunchDetails({ run }: { run: OverviewDetailedRun }) {
  return (
    <div className="run-launch-layout">
      <section aria-label="用户启动输入" className="min-w-0 rounded-lg border border-border bg-muted/30 p-4">
        <h3 className="run-group-heading">
          用户启动输入 <small>LAUNCH INPUT</small>
        </h3>
        <div className="mt-3 grid min-w-0 gap-4 md:grid-cols-2">
          <LaunchValue
            key={`${run.id}:input`}
            field={run.launchInput ?? { label: '输入文本', source: 'flow_runs.input_json', text: null, note: null }}
          />
          <LaunchValue
            key={`${run.id}:params`}
            field={
              run.launchParams ?? { label: '原始启动参数', source: 'flow_runs.params_json', text: null, note: null }
            }
          />
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          输入为已存储记录，不保证原始请求逐字一致；原始参数仅来自 params_json，不使用节点输入或后续 input_json.params
          替代。 已按已知敏感键及文本策略隐藏内容，不保证任意用户文本不含敏感信息。
        </p>
      </section>
      <aside aria-label="运行链路" className="min-w-0 rounded-lg border border-border bg-card p-4">
        <h3 className="run-group-heading">运行链路</h3>
        <dl className="mt-3 space-y-4 text-xs">
          {(
            [
              ['父运行', 'parent_flow_id', run.parentRunId],
              ['根运行', 'root_flow_id', run.rootRunId],
            ] as const
          ).map(([label, column, id]) => (
            <div key={column} className="min-w-0">
              <dt>
                {label}
                <span className="mt-1 block break-all font-mono text-[10px] text-muted-foreground">
                  flow_runs.{column}
                </span>
              </dt>
              <dd className="mt-2 break-all font-mono">
                {id ?? (
                  <span aria-label={`${label}未记录`} className="text-muted-foreground">
                    未记录
                  </span>
                )}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-[11px] text-muted-foreground">
          仅展示服务端记录的链路 ID，不代表已获得关联运行的访问权限。
        </p>
      </aside>
    </div>
  );
}
