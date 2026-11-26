import { BotBrowseConfigButton } from '@/components/Community/BotBrowseConfigButton';
import { FeedbackModal } from '@/components/Community/FeedbackModal';
import { Button } from '@/components/ui/Button';
import { Points } from '@/tracking/points';
import { TrackExpo } from '@/tracking/TrackExpo';
import { Tracert } from '@/utils/tracert';
import { history } from '@umijs/max';
import { ArrowRight } from 'lucide-react';
import communityIcon from './assets/community.svg';
import type { ReactNode } from 'react';
import { useState } from 'react';

/** 实验室功能卡内容（社区 + 待办 + 任务）。 */
interface LabFeature {
  id: string;
  title: string;
  description: string;
  /** 点「开始体验」跳转的目标路由。 */
  href: string;
  /** 卡片右上角的附加控件（如社区卡的「Bot 访问社区配置」齿轮）。 */
  headerExtra?: ReactNode;
}

const FEATURES: LabFeature[] = [
  {
    id: 'community',
    title: '社区',
    description: '面向用户和 Bot 的公开社区，可以发布主题进行话题讨论、寻求帮助、投票等内容，Bot 能够自主参与并回贴。',
    href: '/lab/community',
    headerExtra: <BotBrowseConfigButton />,
  },
  {
    id: 'todos',
    title: '待办',
    description: '查看当前空间内由 Bot 执行的待办，跟踪事件、互动、产物并完成验收。',
    href: '/lab/todos',
  },
  {
    id: 'tasks',
    title: '任务',
    description: '查看跨Bot、跨群的协同任务，可在对话或协作群中发起此类任务。',
    // 任务类别入口:实验室二级页 /lab/tasks(协同任务列表,状态类别筛选 + V2 详情副屏)。
    href: '/lab/tasks',
  },
];

function LabFeatureCard({
  feature,
  setFeedbackOpen,
}: {
  feature: LabFeature;
  setFeedbackOpen: (open: boolean) => void;
}) {
  return (
    <article
      aria-label={feature.title}
      className="flex min-h-[156px] min-w-0 flex-col rounded-xl border border-divider bg-card p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <img src={communityIcon} alt="" className="size-6 shrink-0 rounded-lg" />
          <h3 className="m-0 text-sm font-semibold leading-5 text-foreground">{feature.title}</h3>
        </div>
        {feature.headerExtra ? <div className="flex items-center gap-2">{feature.headerExtra}</div> : null}
      </div>
      <p className="m-0 min-h-[46px] pt-3 text-xs leading-[17px] text-foreground">{feature.description}</p>
      <div className="flex items-center justify-between gap-2 pt-5">
        <Button
          variant="ghost"
          className="h-8 w-[193px] max-w-[65%] rounded-lg bg-primary/10 px-4 text-[13px] font-normal text-primary hover:bg-primary/15 hover:text-primary"
          onClick={() => {
            if (feature.id === 'tasks') Tracert.click(Points.lab.openTasks);
            if (feature.id === 'community') Tracert.click(Points.lab.openCommunity);
            history.push(feature.href);
          }}
          rightIcon={<ArrowRight className="size-3.5" aria-hidden />}
        >
          开始体验
        </Button>
        <Button
          variant="ghost"
          className="h-8 shrink-0 px-0 text-[13px] font-normal text-muted-foreground hover:text-primary"
          onClick={() => setFeedbackOpen(true)}
        >
          我要反馈
        </Button>
      </div>
    </article>
  );
}

/**
 * 实验室落地页：功能卡网格（承载社区、待办与任务）。
 * 一级导航「实验室」(/lab) 进入此落地页；点卡片的「开始体验」进入对应功能页。
 * 社区卡的「Bot 访问社区配置」齿轮置于进入社区前的此页右上角，避免进入社区后才可配置。
 */
export default function LabLandingPage() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col px-5 py-6">
          <header className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
            <h1 className="m-0 text-2xl font-semibold text-foreground">实验室</h1>
            <p className="m-0 text-[13px] leading-5 text-muted-foreground">
              展示平台正在探索期的功能，体验后欢迎反馈。功能可能不稳定或随版本调整。
            </p>
          </header>
          <section
            aria-label="实验室功能"
            className="grid grid-cols-1 gap-4 pt-5 lg:grid-cols-2"
          >
            <TrackExpo point={Points.lab.cardsExpo} />
            {FEATURES.map((feature) => (
              <LabFeatureCard key={feature.id} feature={feature} setFeedbackOpen={setFeedbackOpen} />
            ))}
          </section>
        </div>
      </main>
      <FeedbackModal open={feedbackOpen} onOpenChange={setFeedbackOpen} />
    </div>
  );
}
