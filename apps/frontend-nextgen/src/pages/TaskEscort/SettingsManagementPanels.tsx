import { Check, GitBranch, KeyRound, RotateCcw } from 'lucide-react';
import { Badge, Button } from './ui';

export function SettingsPermissions() {
  return (
    <div className="mt-5 space-y-5">
      <div className="rounded-lg bg-muted/50 p-4 text-xs leading-6 text-muted-foreground">
        权限为静态示例；真实成员、角色与工作区权限不会被本原型读取或修改。
      </div>
      <div className="overflow-x-auto">
        <table className="demo-table">
          <thead>
            <tr>
              <th>角色</th>
              <th>查看运行</th>
              <th>编辑工作流</th>
              <th>审核与应用</th>
              <th>管理设置</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['演示管理员', true, true, true, true],
              ['工作流维护者', true, true, true, false],
              ['只读观察者', true, false, false, false],
            ].map(([role, ...flags]) => (
              <tr key={String(role)}>
                <td>
                  <span className="flex items-center gap-2">
                    <KeyRound size={13} className="text-muted-foreground" />
                    {role}
                  </span>
                </td>
                {flags.map((value, index) => (
                  <td key={index}>
                    {value ? (
                      <Check size={15} className="text-success" />
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SettingsVersions({
  currentVersion,
  onRollback,
}: {
  currentVersion: string;
  onRollback: (version: string) => void;
}) {
  return (
    <div className="mt-5 space-y-3">
      {[
        {
          version: 'v2.4',
          date: '2026-11-17 09:30',
          description: '为数据采集节点增加超时与指数退避重试',
        },
        {
          version: 'v2.3',
          date: '2026-11-15 14:20',
          description: '补充结构化输出校验与人工确认分支',
        },
        {
          version: 'v2.2',
          date: '2026-11-12 10:00',
          description: '初始化任务护航节点与运行观察配置',
        },
      ].map((item) => (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-4" key={item.version}>
          <span className="grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground">
            <GitBranch size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-xs font-medium">
              {item.version}
              {currentVersion === item.version && <Badge tone="success">当前版本</Badge>}
            </div>
            <p className="my-1.5 text-xs">{item.description}</p>
            <p className="font-mono text-[10px] text-muted-foreground">{item.date} · 演示管理员</p>
          </div>
          {currentVersion !== item.version && (
            <Button size="sm" variant="outline" onClick={() => onRollback(item.version)}>
              <RotateCcw size={12} />
              模拟回滚
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}
