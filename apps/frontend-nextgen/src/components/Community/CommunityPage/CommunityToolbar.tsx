import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type { CommunityTopicFilter } from '@/domain/community/types';
import type { useCommunity } from '@/hooks/useCommunity';
import { CircleHelp, Plus, Search, UserRound } from 'lucide-react';
import { CommunityFollowIcon } from '../CommunityFollowIcon';
import { TopicTypeIcon } from '../TopicTypeIcon';

type CommunityView = CommunityTopicFilter | 'following' | 'help-new';
const topicTypeOptions = [
  { value: 'all' as const, label: '全部' },
  { value: 'following' as const, label: '关注', icon: <CommunityFollowIcon className="size-3.5" aria-hidden /> },
  ...(['discussion', 'share', 'poll', 'help'] as const).map((value, index) => ({
    value,
    label: ['讨论', '分享', '投票', '求助'][index],
    icon: <TopicTypeIcon topicType={value} className="size-3.5" />,
  })),
  { value: 'help-new' as const, label: '求助_new', icon: <CircleHelp className="size-3.5" aria-hidden /> },
];

export function CommunityToolbar({
  community,
  helpActive,
  onHelpActiveChange,
  onPersonalCenter,
  onPublish,
}: {
  community: ReturnType<typeof useCommunity>;
  helpActive: boolean;
  onHelpActiveChange: (active: boolean) => void;
  onPersonalCenter: () => void;
  onPublish: () => void;
}) {
  const topicView: CommunityView = helpActive
    ? 'help-new'
    : community.scope === 'following'
    ? 'following'
    : community.topicType;
  return (
    <section className="mt-4 flex flex-wrap items-center gap-3" aria-label="社区主题筛选">
      <div className="max-w-full overflow-x-auto sm:flex-1">
        <Segmented<CommunityView>
          value={topicView}
          onChange={(value) => {
            onHelpActiveChange(value === 'help-new');
            // Mock 入口不是 BBS 主题类型，绝不下发给既有社区查询。
            if (value === 'help-new') return;
            if (value === 'following') {
              community.setScope('following');
              return;
            }
            if (community.scope === 'following') community.setScope('all');
            community.setTopicType(value);
          }}
          aria-label="主题类型"
          options={topicTypeOptions}
          activeOptionClassName="bg-primary/10 text-primary hover:bg-primary/10"
          className="h-9 w-max min-w-full bg-background"
        />
      </div>
      {!helpActive && (
        <div className="relative w-full min-w-[160px] sm:ml-auto sm:w-40 sm:flex-none">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            className="h-9 rounded-lg border-border bg-muted/30 pl-10 pr-4 text-sm"
            value={community.query}
            onChange={(event) => community.setQuery(event.target.value)}
            placeholder="搜索主题..."
            aria-label="搜索社区主题"
          />
        </div>
      )}
      <IconButton
        label="个人中心"
        icon={
          community.currentUser ? (
            <Avatar name={community.currentUser.displayName} src={community.currentUser.avatarUrl} size={40} />
          ) : (
            <UserRound className="size-4" aria-hidden />
          )
        }
        className="ml-auto size-10 rounded-full sm:ml-0"
        onClick={onPersonalCenter}
      />
      {!helpActive && (
        <Button className="h-9 px-4 text-xs" leftIcon={<Plus className="size-3.5" aria-hidden />} onClick={onPublish}>
          发帖
        </Button>
      )}
    </section>
  );
}
