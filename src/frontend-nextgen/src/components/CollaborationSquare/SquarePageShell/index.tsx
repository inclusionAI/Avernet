import SquareBotCard from '@/components/CollaborationSquare/BotCard';
import { BotProfileModal } from '@/components/CollaborationSquare/BotProfileModal';
import GroupCard from '@/components/CollaborationSquare/GroupCard';
import { GroupMembersModal } from '@/components/CollaborationSquare/GroupMembersModal';
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { history } from '@umijs/max';
import { Bot, RefreshCw, Users } from 'lucide-react';

function LoadingState({ resource }: { resource: SquareResource }) {
  return (
    <div
      aria-label={`正在加载公开${resource === 'bot' ? ' Bot' : '协作群'}`}
      className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
    >
      {[0, 1, 2].map((item) => (
        <Card key={item}>
          <Skeleton.Card />
        </Card>
      ))}
    </div>
  );
}

export function SquarePageShell({ resource }: { resource: SquareResource }) {
  const square = useCollaborationSquare(resource);
  const isBot = resource === 'bot';
  const items = isBot ? square.visibleBots : square.visibleGroups;
  return (
    <main className="app-scrollbar h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-7xl flex-col space-y-5 p-4 sm:p-6 lg:p-8">
        <PageHeader
          title="协作广场"
          description={
            isBot
              ? '可按 Bot 名称或 Owner 用户名称搜索公开 Bot，也可通过能力描述进行智能发现，并以当前用户身份发起好友申请。'
              : '发现协作群，支持基于公开协作群快速创建新会话。'
          }
        />
        <div className="flex flex-wrap gap-2" aria-label="协作广场资源导航">
          <Button
            variant={isBot ? 'primary' : 'secondary'}
            onClick={() => history.push('/collaboration-square/bots')}
            leftIcon={<Bot aria-hidden className="h-4 w-4" />}
          >
            公开 Bot
          </Button>
          <Button
            variant={!isBot ? 'primary' : 'secondary'}
            onClick={() => history.push('/collaboration-square/groups')}
            leftIcon={<Users aria-hidden className="h-4 w-4" />}
          >
            公开协作群
          </Button>
        </div>
        <SquareSearchBar
          resource={resource}
          query={isBot ? square.botQuery : square.groupQuery}
          mode={square.botSearchMode}
          onQueryChange={(query) => square.setQuery(resource, query)}
          onModeChange={isBot ? square.setBotSearchMode : undefined}
        />
        {square.loading && <LoadingState resource={resource} />}
        {!square.loading && square.error && (
          <Card>
            <Empty
              title="协作广场加载失败"
              description={square.error}
              action={
                <Button onClick={() => void square.load()} leftIcon={<RefreshCw aria-hidden className="h-4 w-4" />}>
                  重新加载
                </Button>
              }
            />
          </Card>
        )}
        {!square.loading && !square.error && items.length === 0 && (
          <Card>
            <Empty
              title={isBot ? '没有找到公开 Bot' : '没有找到公开协作群'}
              description={isBot ? '尝试更换关键词或清除搜索。' : '尝试更换群名称或清除搜索。'}
            />
          </Card>
        )}
        {!square.loading && !square.error && items.length > 0 && isBot && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {square.visibleBots.map((bot) => (
              <SquareBotCard
                key={bot.id}
                bot={bot}
                busy={square.busyKeys.includes(`bot:${bot.id}`)}
                onShare={(item) => square.share('bot', item.id)}
                onPrimaryAction={square.primaryBotAction}
              />
            ))}
          </div>
        )}
        {!square.loading && !square.error && items.length > 0 && !isBot && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {square.visibleGroups.map((group) => (
              <GroupCard
                key={group.id}
                group={group}
                busy={square.busyKeys.includes(`group:${group.id}`)}
                onOpenMembers={(item) => void square.openGroupMembers(item)}
                onShare={(item) => square.share('group', item.id)}
                onCreateSession={square.createGroupSession}
              />
            ))}
          </div>
        )}
        <BotProfileModal
          open={Boolean(square.selectedBotId)}
          profile={square.botProfile}
          loading={square.detailLoading}
          onClose={square.closeBotProfile}
          onCopyId={(id) => void square.copyBotId(id)}
        />
        <GroupMembersModal
          open={Boolean(square.selectedGroupId)}
          group={square.selectedGroup}
          members={square.groupMembers}
          loading={square.detailLoading}
          onClose={square.closeGroupMembers}
        />
      </div>
    </main>
  );
}
