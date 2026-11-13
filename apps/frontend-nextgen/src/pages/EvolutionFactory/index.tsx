import { Points } from '@/tracking/points';
import { TrackExpo } from '@/tracking/TrackExpo';
import { useLocation } from '@umijs/max';
import { useState } from 'react';
import DomainList from './DomainList';
import { EvolutionTabs } from './shared';
import TaskList from './TaskList';
import { TaskTypePicker } from './TaskTypePicker';
import { useEvolutionContext } from './useEvolutionContext';
import VersionList from './VersionList';

function EvolutionFactoryContent() {
  const location = useLocation();
  const selectedTab = new URLSearchParams(location.search).get('tab');
  const tab = selectedTab === 'versions' ? 'versions' : selectedTab === 'templates' ? 'templates' : 'tasks';
  const [open, setOpen] = useState(false);
  return (
    <main className="app-scrollbar h-full overflow-y-auto bg-background p-5">
      <header className="mb-5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h1 className="m-0 text-2xl font-semibold leading-8 text-foreground">智能进化</h1>
        <p className="m-0 text-[13px] leading-[22px] text-muted-foreground">
          围绕 Bot 的真实运行数据完成「诊断 → 优化 → 验证」的进化闭环
        </p>
      </header>
      <section className="relative min-w-0">
        <EvolutionTabs tab={tab} className="mb-5" />
        {tab === 'tasks' ? (
          <TaskList onCreate={() => setOpen(true)} />
        ) : tab === 'templates' ? (
          <>
            <TrackExpo point={Points.evolution.entryCardsExpo} />
            <DomainList />
          </>
        ) : (
          <VersionList />
        )}
      </section>
      <TaskTypePicker open={open} onOpenChange={setOpen} />
    </main>
  );
}

export default function EvolutionFactory() {
  const context = useEvolutionContext();
  return <EvolutionFactoryContent key={context.key} />;
}
