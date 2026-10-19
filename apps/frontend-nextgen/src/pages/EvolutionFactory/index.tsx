import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { taskTypes } from '@/domain/evolution/types';
import { history, useLocation } from '@umijs/max';
import { ChartColumn, ChevronRight, Repeat2, Search, Stethoscope, TrendingUp } from 'lucide-react';
import { useState } from 'react';
import DomainList from './DomainList';
import { EvolutionHeader, typeTone } from './shared';
import TaskList from './TaskList';
import { useEvolutionContext } from './useEvolutionContext';
import VersionList from './VersionList';

const taskIcons = {
  diagnose: Search,
  bench: ChartColumn,
  bench_optimize: TrendingUp,
  optimize: Stethoscope,
  full: Repeat2,
};

const taskHints = {
  diagnose: '只诊断不修改 · 适合周期性体检',
  bench: '产出可作为 Bench 优化任务的输入',
  bench_optimize: '使用已发布的训练与测试评测模板',
  optimize: '需关联已完成诊断及目标规划的任务',
  full: '一句话目标 · 自动规划、优化和验证',
};

function EvolutionFactoryContent() {
  const location = useLocation();
  const selectedTab = new URLSearchParams(location.search).get('tab');
  const tab = selectedTab === 'versions' ? 'versions' : selectedTab === 'templates' ? 'templates' : 'tasks';
  const [open, setOpen] = useState(false);
  return (
    <main className="h-full overflow-y-auto bg-background p-4 sm:p-6 lg:px-10">
      <div className="w-full min-w-0 space-y-5">
        <EvolutionHeader tab={tab} />
        {tab === 'tasks' ? (
          <TaskList onCreate={() => setOpen(true)} />
        ) : tab === 'templates' ? (
          <DomainList />
        ) : (
          <VersionList />
        )}
        <Modal open={open} onOpenChange={setOpen}>
          <ModalContent size="lg" className="max-w-[760px]" aria-describedby={undefined}>
            <ModalHeader>
              <ModalTitle className="text-base">新建任务</ModalTitle>
            </ModalHeader>
            <p className="text-sm text-muted-foreground">
              请选择任务类型 —— 三种分类构成「诊断 → 优化 → 验证」的进化工作流
            </p>
            {['诊断类', '优化类', '全流程类'].map((group, index) => (
              <section
                key={group}
                className={`space-y-3 rounded-xl border p-4 ${
                  index === 0
                    ? 'border-primary/20 bg-primary/[0.03]'
                    : index === 1
                    ? 'border-warning/20 bg-warning/[0.03]'
                    : 'border-[hsl(var(--purple)/0.2)] bg-[hsl(var(--purple)/0.03)]'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2.5">
                  <span
                    className={`flex h-7 w-7 items-center justify-center rounded-lg ${
                      index === 0
                        ? 'bg-primary/10 text-primary'
                        : index === 1
                        ? 'bg-warning/10 text-warning'
                        : 'bg-[hsl(var(--purple)/0.1)] text-[hsl(var(--purple))]'
                    }`}
                  >
                    {index === 0 ? (
                      <Search size={16} />
                    ) : index === 1 ? (
                      <TrendingUp size={16} />
                    ) : (
                      <Repeat2 size={16} />
                    )}
                  </span>
                  <h2
                    className={`text-[15px] font-semibold ${
                      index === 0 ? 'text-primary' : index === 1 ? 'text-warning' : 'text-[hsl(var(--purple))]'
                    }`}
                  >
                    {group}
                  </h2>
                  <span className="text-xs text-muted-foreground">
                    {['定位问题、量化能力水平', '基于诊断结论定向优化', '按目标规划、优化与验证'][index]}
                  </span>
                </div>
                {taskTypes
                  .filter((type) => type.group === group)
                  .map((type) => {
                    const Icon = taskIcons[type.type];
                    return (
                      <Button
                        variant="ghost"
                        style={{ height: 'auto', whiteSpace: 'normal' }}
                        type="button"
                        key={type.type}
                        aria-label={type.label}
                        className="flex w-full items-center justify-between gap-3 rounded-lg border border-border bg-card p-3.5 text-left hover:border-primary"
                        onClick={() => history.push(`/evolution-factory/new?type=${type.type}`)}
                      >
                        <span className="flex items-start gap-4">
                          <span
                            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${typeTone(
                              type.type,
                            )}`}
                          >
                            <Icon size={22} />
                          </span>
                          <span>
                            <strong className="text-[14px]">{type.label}</strong>
                            <span className="mt-0.5 block text-[13px] leading-relaxed text-muted-foreground">
                              {type.description}
                            </span>
                            <span className="mt-1 block text-xs text-muted-foreground">{taskHints[type.type]}</span>
                          </span>
                        </span>
                        <ChevronRight size={16} />
                      </Button>
                    );
                  })}
              </section>
            ))}
          </ModalContent>
        </Modal>
      </div>
    </main>
  );
}

export default function EvolutionFactory() {
  const context = useEvolutionContext();
  return <EvolutionFactoryContent key={context.key} />;
}
