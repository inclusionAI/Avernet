import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { taskTypes } from '@/domain/evolution/types';
import { history, useLocation } from '@umijs/max';
import { ChartColumn, ChevronRight, Repeat2, Search, Stethoscope, TrendingUp } from 'lucide-react';
import { useState } from 'react';
import DomainList from './DomainList';
import { EvolutionTabs, typeTone } from './shared';
import TaskList from './TaskList';
import { useEvolutionContext } from './useEvolutionContext';
import VersionList from './VersionList';
import { Points } from '@/tracking/points';
import { TrackExpo } from '@/tracking/TrackExpo';
import { Tracert } from '@/utils/tracert';

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

/** 新建任务弹窗的三段式分组（诊断 → 优化 → 全流程），色调沿用既有语义 token。 */
const groupTones = [
  { chips: 'bg-primary/10 text-primary', heading: 'text-primary', border: 'border-primary/20' },
  { chips: 'bg-warning/10 text-warning', heading: 'text-warning', border: 'border-warning/20' },
  {
    chips: 'bg-[hsl(var(--purple)/0.1)] text-[hsl(var(--purple))]',
    heading: 'text-[hsl(var(--purple))]',
    border: 'border-[hsl(var(--purple)/0.2)]',
  },
];

function EvolutionFactoryContent() {
  const location = useLocation();
  const selectedTab = new URLSearchParams(location.search).get('tab');
  const tab = selectedTab === 'versions' ? 'versions' : selectedTab === 'templates' ? 'templates' : 'tasks';
  const [open, setOpen] = useState(false);
  return (
    // 与 Bot 管理首页同一画布规范：灰底画布 + PageHeader + 单张白卡承载 Tabs → 筛选行 → 结果面板 → 分页。
    <main className="app-scrollbar h-full overflow-y-auto bg-page-canvas">
      <div className="w-full p-5 sm:p-6">
        <PageHeader title="智能进化" description="围绕 Bot 的真实运行数据完成「诊断 → 优化 → 验证」的进化闭环" />
        <section className="mt-5 rounded-xl border border-border/60 bg-card p-4 sm:p-6">
          <EvolutionTabs tab={tab} className="mb-4" />
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
        <Modal open={open} onOpenChange={setOpen}>
          <ModalContent size="lg" className="max-w-[760px]" aria-describedby={undefined}>
            <ModalHeader>
              <ModalTitle className="text-base">新建任务</ModalTitle>
            </ModalHeader>
            <p className="text-sm text-muted-foreground">
              请选择任务类型 —— 三种分类构成「诊断 → 优化 → 验证」的进化工作流
            </p>
            {['诊断类', '优化类', '全流程类'].map((group, index) => {
              const tone = groupTones[index];
              return (
                <section key={group} className={`space-y-2.5 rounded-xl border p-4 ${tone.border}`}>
                  <div className="flex flex-wrap items-center gap-2.5">
                    <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${tone.chips}`}>
                      {index === 0 ? (
                        <Search size={16} />
                      ) : index === 1 ? (
                        <TrendingUp size={16} />
                      ) : (
                        <Repeat2 size={16} />
                      )}
                    </span>
                    <h2 className={`text-[15px] font-semibold ${tone.heading}`}>{group}</h2>
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
                          className="flex w-full items-center justify-between gap-3 rounded-lg border border-border/60 bg-card p-3.5 text-left transition-colors hover:border-primary hover:bg-primary/[0.03]"
                          onClick={() => {
                            Tracert.click(Points.evolution.createEntry);
                            history.push(`/evolution-factory/new?type=${type.type}`);
                          }}
                        >
                          <span className="flex min-w-0 items-start gap-3.5">
                            <span
                              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${typeTone(
                                type.type,
                              )}`}
                            >
                              <Icon size={20} />
                            </span>
                            <span className="min-w-0">
                              <span className="block text-sm font-medium text-foreground">{type.label}</span>
                              <span className="mt-0.5 block text-[13px] leading-relaxed text-muted-foreground">
                                {type.description}
                              </span>
                              <span className="mt-1 block text-xs text-content-icon">{taskHints[type.type]}</span>
                            </span>
                          </span>
                          <ChevronRight size={16} className="shrink-0 text-content-icon" />
                        </Button>
                      );
                    })}
                </section>
              );
            })}
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