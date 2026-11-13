import { Button } from '@/components/ui';
import type { GroupContextView } from '@/services/workspace/groupContextService';
import { useState } from 'react';
import { ManagePanelTabs } from './ManagePanelTabs';
import { SharedInfoCard } from './SharedInfoCard';
import { useSharedInfo } from '../../hooks/useSharedInfo';

const sections = [
  ['group', '群组级'],
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
  const [scope, setScope] = useState<(typeof sections)[number][0]>('group');
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

  const activeScope = sessionId ? scope : 'group';
  const entries = result.filter((item) => item.scope_level === activeScope);
  const options = sections.map(([value, label]) => ({
    value,
    label,
    count: result.filter((item) => item.scope_level === value).length,
  }));

  return (
    <div className="space-y-3">
      {sessionId && <ManagePanelTabs value={scope} options={options} onChange={setScope} />}
      {entries.length === 0 && <p className="text-sm text-muted-foreground">暂无可查看的共享信息</p>}
      {entries.map((item) => (
        <SharedInfoCard
          key={JSON.stringify([item.scope_level, item.scope, item.name])}
          item={item}
          creator={names.get(item.creator) ?? item.creator}
          sessionId={sessionId}
        />
      ))}
    </div>
  );
}

export function SharedInfoList({ view, viewerId, names }: SharedInfoListProps) {
  const key = JSON.stringify([view.groupId, view.sessionId, view.view_bot_id, viewerId]);
  return <SharedInfoListContent key={key} view={view} names={names} />;
}
