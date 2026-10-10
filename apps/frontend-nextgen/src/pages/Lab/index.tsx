import { PageHeader } from '@/components/Common/PageHeader';
import { BotBrowseConfigButton } from '@/components/Community/BotBrowseConfigButton';
import { FeedbackModal } from '@/components/Community/FeedbackModal';
import { Button } from '@/components/ui/Button';
import { history } from '@umijs/max';
import type { LucideIcon } from 'lucide-react';
import { ArrowRight, Megaphone, MessagesSquare } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';

/** 实验室功能卡内容（当前仅社区；任务等待后续实验性功能加入）。文案对齐原型 …/lab。 */
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
    <article className="flex flex-col gap-5 rounded-xl border border-border bg-card p-6">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary" aria-hidden>
            <Icon className="size-5" />
          </span>
          <h2 className="m-0 text-lg font-semibold text-foreground">{feature.title}</h2>
        </div>
        {feature.headerExtra ? <div className="flex items-center gap-2">{feature.headerExtra}</div> : null}
      </div>
      <p className="m-0 text-sm leading-6 text-muted-foreground">{feature.description}</p>
      <div className="mt-auto flex flex-wrap items-center gap-3 pt-1">
        <Button onClick={() => history.push(feature.href)} rightIcon={<ArrowRight className="size-4" aria-hidden />}>
          开始体验
        </Button>
        <Button
          variant="ghost"
          leftIcon={<Megaphone className="size-4" aria-hidden />}
          onClick={() => setFeedbackOpen(true)}
        >
          我要反馈
        </Button>
      </div>
    </article>
  );
}

/**
 * 实验室落地页：功能卡网格（当前承载社区；任务等待后续实验性功能加入）。
 * 一级导航「实验室」(/lab) 进入此落地页；点卡片的「开始体验」进入对应功能页。
 * 社区卡的「Bot 访问社区配置」齿轮置于进入社区前的此页右上角，避免进入社区后才可配置。
 */
export default function LabLandingPage() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  return (
    <div className="flex h-full flex-col bg-muted">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6 lg:p-8">
          <PageHeader
            title="实验室"
            description="展示平台正在探索期的功能，体验后欢迎反馈。功能可能不稳定或随版本调整。"
          />
          <section aria-label="实验室功能" className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
