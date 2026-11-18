import { ArrowRight } from 'lucide-react';
import type { ViewProps } from './types';
import { Badge, Button, Panel, StatusBadge } from './ui';

export function DashboardWorkflowHealth({ demo }: ViewProps) {
  return (
    <Panel
      title="工作流健康概览"
      description="从全局定位需要优先关注的工作流"
      action={
        <Button size="sm" variant="ghost" onClick={() => demo.navigate('runs')}>
          全部运行
          <ArrowRight size={13} />
        </Button>
      }
    >
      <div className="overflow-x-auto">
        <table className="demo-table">
          <thead>
            <tr>
              <th>工作流 / Bot</th>
              <th>健康状态</th>
              <th>运行数</th>
              <th>成功率</th>
              <th>当前版本</th>
              <th>最近运行</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {demo.workflows.slice(0, 4).map((workflow) => (
              <tr key={workflow.id}>
                <td>
                  <div className="font-medium">{workflow.name}</div>
                  <div className="mt-1 text-[10px] text-muted-foreground">{workflow.bot}</div>
                </td>
                <td>
                  <StatusBadge status={workflow.status} />
                </td>
                <td className="tabular-nums">{workflow.runCount.toLocaleString()}</td>
                <td
                  className={`tabular-nums ${
                    workflow.successRate === null
                      ? 'text-muted-foreground'
                      : workflow.successRate < 90
                      ? 'text-warning'
                      : 'text-success'
                  }`}
                >
                  {workflow.successRate === null ? '—' : `${workflow.successRate}%`}
                </td>
                <td>
                  <Badge tone="neutral">{workflow.version}</Badge>
                </td>
                <td className="text-muted-foreground">{workflow.updatedAt ?? '—'}</td>
                <td>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      demo.navigate('runs');
                      demo.setWorkflowId(workflow.id);
                    }}
                  >
                    查看
                    <ArrowRight size={12} />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
