/**
 * FriendsTab - 已添加好友 Tab
 *
 * 展示当前 Bot 的好友列表，支持搜索过滤
 */

import BotAvatar from '@/components/BotAvatar';
import Empty from '@/components/Empty';
import { SearchInput } from '@/components/ui/search-input';
import type { FriendInfo } from '@/stores/friendStore';
import { cn } from '@/utils/utils';
import { Heart, Loader2 } from 'lucide-react';
import React, { useMemo, useState } from 'react';

interface FriendsTabProps {
  friends: FriendInfo[];
  isLoading: boolean;
}

const FriendsTab: React.FC<FriendsTabProps> = ({ friends, isLoading }) => {
  const [searchQuery, setSearchQuery] = useState('');

  // 过滤好友
  const filteredFriends = useMemo(() => {
    if (!searchQuery?.trim()) return friends;
    const query = searchQuery?.toLowerCase();
    return friends.filter(
      (friend) =>
        friend.bot_name?.toLowerCase().includes(query) ||
        friend.summary?.toLowerCase().includes(query),
    );
  }, [friends, searchQuery]);

  return (
    <div className="p-4">
      {/* 搜索框 */}
      <div className="mb-3">
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="搜索好友..."
        />
      </div>

      {/* 好友列表 */}
      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
        </div>
      ) : filteredFriends.length === 0 ? (
        <Empty
          size="sm"
          icon={<Heart />}
          title={searchQuery ? '未找到匹配的好友' : '暂无好友'}
          description="切换到「添加好友」或「好友申请」添加好友"
          className="py-12"
        />
      ) : (
        <div className="divide-y divide-slate-100">
          {filteredFriends.map((friend) => (
            <div
              key={friend.bot_uuid}
              className="flex items-center gap-3 px-3 py-3 hover:bg-slate-50 rounded-lg"
            >
              <div className="relative">
                <BotAvatar
                  type="expert"
                  size="sm"
                  name={friend.bot_name}
                  botId={friend.bot_uuid.split(':')[0]}
                  avatarUrl={friend.avatar_url}
                />
                {/* 在线状态指示器 */}
                <div
                  className={cn(
                    'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-white',
                    friend.is_online ? 'bg-green-500' : 'bg-slate-300',
                  )}
                />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-700 truncate">
                  {friend.bot_name}
                </p>
                {friend.summary && (
                  <p className="text-xs text-slate-400 truncate">
                    {friend.summary}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default FriendsTab;
