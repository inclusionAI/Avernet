import { Button, Card } from '@/components/ui';
import type { GroupContextView } from '@/services/workspace/groupContextService';
import { useSharedInfo } from '../../hooks/useSharedInfo';

const sections = [
  ['group', '群级'],
  ['session', '会话级'],
  ['round', '轮次级'],
] as const;

interface SharedInfoListProps {
  view: GroupContextView;
  viewerId?: string | null;
  names: ReadonlyMap<string, string>;
}

function SharedInfoListContent({ view, names }: Omit<SharedInfoListProps, 'viewerId'>) {
  const { sessionId } = view;
  const { result, reload } = useSharedInfo(view);

  if (result === undefined) return <p role="status" className="text-sm text-muted-foreground">正在加载共享信息…</p>;
  if (result === null) {
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p>共享信息加载失败</p>
        <Button variant="outline" size="sm" onClick={reload}>重试</Button>
      </div>
    );
  }

  const items = sessionId ? result : result.filter((item) => item.scope_level === 'group');
  if (!items.length) return <p className="text-sm text-muted-foreground">暂无可查看的共享信息</p>;

  return (
    <div className="space-y-5">
      {sections.map(([scope, label]) => {
        const entries = items.filter((item) => item.scope_level === scope);
        if (!entries.length) return null;
        return (
          <section key={scope} className="space-y-2">
            {sessionId && (
              <h3 className="flex items-center gap-2 text-sm font-semibold">
                {label}
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{entries.length}</span>
              </h3>
            )}
            {entries.map((item) => {
              const creator = names.get(item.creator) ?? item.creator;
              const time = new Date(item.gmt_modified);
              const displayTime = Number.isNaN(time.getTime()) ? item.gmt_modified : time.toLocaleString();
              return (
                <Card key={JSON.stringify([scope, item.scope, item.name])} className="space-y-2 bg-muted/40 p-3">
                  <h4 className="break-words text-sm font-semibold">{item.name}</h4>
                  {scope === 'round' && sessionId && (
                    <p className="break-all text-xs text-muted-foreground">轮次 {item.scope.slice(sessionId.length + 1)}</p>
                  )}
                  <p className="whitespace-pre-wrap break-words text-sm">{item.content}</p>
                  <p className="break-words text-xs text-muted-foreground">{creator} 更新于 {displayTime}</p>
                </Card>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

export function SharedInfoList({ view, viewerId, names }: SharedInfoListProps) {
  const key = JSON.stringify([view.groupId, view.sessionId, view.view_bot_id, viewerId]);
  return <SharedInfoListContent key={key} view={view} names={names} />;
}
