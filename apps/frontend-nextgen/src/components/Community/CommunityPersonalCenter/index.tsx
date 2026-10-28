import { Segmented } from '@/components/ui/Segmented';
import { Pagination } from '@/components/ui/Pagination';
import { Button } from '@/components/ui/Button';
import { ArrowLeft } from 'lucide-react';
import type {
  PersonalCenterActivityType,
  PersonalCenterActorScope,
  PersonalCenterTargetFilter,
} from '@/domain/community/types';
import { usePersonalCenter } from '@/hooks/usePersonalCenter';
import { ActivityList } from './ActivityList';
import { StatsCards } from './StatsCards';
import {
  personalCenterActivityOptions,
  personalCenterScopeOptions,
  personalCenterTargetOptions,
} from './model';

export function CommunityPersonalCenter({
  onBack,
  onOpenTopic,
}: {
  onBack: () => void;
  onOpenTopic: (topicId: string) => void;
}) {
  const center = usePersonalCenter();

  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-6xl flex-col px-4 py-6 sm:px-6 lg:px-10">
          <header className="m-0 flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={onBack} aria-label="返回社区列表">
              <ArrowLeft className="size-4" aria-hidden />
            </Button>
            <h2 className="m-0 text-lg font-semibold text-foreground">个人中心</h2>
          </header>

          <section className="mt-4 grid gap-3 md:grid-cols-[320px_1fr]">
            <Segmented<PersonalCenterActorScope>
              value={center.actorScope}
              onChange={center.setActorScope}
              options={personalCenterScopeOptions.map((item) => ({ ...item }))}
              aria-label="身份筛选"
              className="h-9"
            />
            <div className="flex justify-end">
              <Segmented<PersonalCenterTargetFilter>
                value={center.targetType}
                onChange={center.setTargetType}
                options={personalCenterTargetOptions.map((item) => ({ ...item }))}
                aria-label="内容类型"
                className="h-9"
              />
            </div>
          </section>

          <div className="mt-4">
            <StatsCards
              stats={center.aggregateStats}
              identities={center.identityStats}
              loading={center.loadingStats || (center.subscriptionLoading && center.actorScope !== 'HUMAN')}
            />
          </div>

          <section className="mt-5 flex justify-start">
            <Segmented<PersonalCenterActivityType>
              value={center.activityType}
              onChange={center.setActivityType}
              options={personalCenterActivityOptions}
              aria-label="活动类型"
              className="h-9"
            />
          </section>

          <div className="mt-4">
            <ActivityList
              items={center.activityPage.items}
              loading={center.loading}
              error={center.error}
              onRetry={center.reload}
              onOpenTopic={onOpenTopic}
            />
          </div>

          <div className="mt-4">
            <Pagination
              current={center.page}
              pageSize={center.pageSize}
              total={center.activityPage.total}
              onChange={center.goToPage}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
