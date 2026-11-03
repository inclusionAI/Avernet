import { PageHeader } from '@/components/Common/PageHeader';
import { BotBrowseConfigButton } from '@/components/Community/BotBrowseConfigButton';
import { FeedbackModal } from '@/components/Community/FeedbackModal';
import { Button } from '@/components/ui/Button';
import { history } from '@umijs/max';
import type { LucideIcon } from 'lucide-react';
import { ArrowRight, MessagesSquare, Target } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { TrackExpo } from '@/tracking/TrackExpo';

/** 实验室功能卡内容（社区 + 任务）。文案对齐原型 TeamClaw-demo …/Lab(最新任务产品 PRD)。 */
interface LabFeature {
  id: string;
  title: string;
  description: string;
  icon: LucideIcon;
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
    icon: MessagesSquare,
    href: '/lab/community',
    headerExtra: <BotBrowseConfigButton />,
  },
  {
    id: 'tasks',
    title: '任务',
    description: '查看跨Bot、跨群的协同任务，可在对话或协作群中发起此类任务。',
    icon: Target,
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
  const Icon = feature.icon;
  return (
    <article
    aria-label={feature.title}
    className="flex flex-col gap-5 rounded-xl border border-border bg-card p-6 shadow-sm transition-shadow hover:shadow-md"
  >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary" aria-hidden>
            <Icon className="size-5" />
          </span>
          <h3 className="m-0 text-base font-semibold text-foreground">{feature.title}</h3>
        </div>
        {feature.headerExtra ? <div className="flex items-center gap-2">{feature.headerExtra}</div> : null}
      </div>
      <p className="m-0 min-h-12 text-sm leading-6 text-muted-foreground">{feature.description}</p>
      <div className="flex items-center justify-between">
        <Button
          className="h-9 px-4 text-xs"
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
          className="h-6 px-0 text-xs font-medium text-muted-foreground hover:text-primary"
          onClick={() => setFeedbackOpen(true)}
        >
          我要反馈
        </Button>
      </div>
    </article>
  );
}

/**
 * 实验室落地页：功能卡网格（承载社区与任务）。
 * 一级导航「实验室」(/lab) 进入此落地页；点卡片的「开始体验」进入对应功能页。
 * 社区卡的「Bot 访问社区配置」齿轮置于进入社区前的此页右上角，避免进入社区后才可配置。
 * 任务卡进入 /lab/tasks：跨Bot、跨群协同任务列表(状态类别筛选 + V2 详情副屏)。
 */
export default function LabLandingPage() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col gap-6 px-4 py-6 sm:px-6 lg:px-10">
          <PageHeader
            title="实验室"
            description="展示平台正在探索期的功能，体验后欢迎反馈。功能可能不稳定或随版本调整。"
          />
          <section
            aria-label="实验室功能"
            className="grid grid-cols-1 gap-4 xl:grid-cols-[repeat(auto-fill,minmax(420px,1fr))]"
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
