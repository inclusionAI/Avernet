import { Points } from '@/tracking/points';
import { TrackExpo } from '@/tracking/TrackExpo';
import { useLocation } from '@umijs/max';
import { useState } from 'react';
import DomainList from './DomainList';
import RecoveryWorkspace from './Recovery';
import { EvolutionModeSwitch, EvolutionTabs, sessionRecoveryEnabled } from './shared';
import TaskList from './TaskList';
import { TaskTypePicker } from './TaskTypePicker';
import { useEvolutionContext } from './useEvolutionContext';
import VersionList from './VersionList';

function EvolutionFactoryContent({ context }: { context: ReturnType<typeof useEvolutionContext> }) {
  const location = useLocation();
  const selectedTab = new URLSearchParams(location.search).get('tab');
  const recoveryEnabled = sessionRecoveryEnabled();
  const tab =
    selectedTab === 'versions' || selectedTab === 'templates' || (selectedTab === 'recovery' && recoveryEnabled)
      ? selectedTab
      : 'tasks';
  const [open, setOpen] = useState(false);
  return (
    <main className="app-scrollbar h-full overflow-y-auto bg-background p-5">
      {recoveryEnabled ? (
        <header className="mb-5 space-y-3">
          <h1 className="m-0 text-2xl font-semibold leading-8 text-foreground">智能进化</h1>
          <EvolutionModeSwitch tab={tab} />
        </header>
      ) : (
        <header className="mb-5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h1 className="m-0 text-2xl font-semibold leading-8 text-foreground">智能进化</h1>
          <p className="m-0 text-[13px] leading-[22px] text-muted-foreground">
            围绕 Bot 的真实运行数据完成「诊断 → 优化 → 验证」的进化闭环
          </p>
        </header>
      )}
      <section className="relative min-w-0">
        <EvolutionTabs tab={tab} className="mb-5" />
        {tab === 'tasks' ? (
          <TaskList onCreate={() => setOpen(true)} />
        ) : tab === 'templates' ? (
          <>
            <TrackExpo point={Points.evolution.entryCardsExpo} />
            <DomainList />
          </>
        ) : tab === 'recovery' ? (
          <RecoveryWorkspace
            spaceId={context.spaceId}
            ready={context.ready}
            loading={context.loading}
            error={context.error}
          />
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
  return <EvolutionFactoryContent key={context.key} context={context} />;
}
