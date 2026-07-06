/**
 * GroupAvatarGrid - 群组成员头像四宫格
 *
 * 1人：单头像全尺寸
 * 2人：左右各半
 * 3人：上排2个 + 下排1个居中
 * 4+人：2×2 四宫格（取前4个）
 */

import BotAvatar from '@/components/BotAvatar';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import React from 'react';

export interface GridAvatarItem {
  name: string;
  type: 'bot' | 'user' | string;
  botUuid?: string;
  avatar?: string;
}

const Cell: React.FC<{ item: GridAvatarItem }> = ({ item }) => {
  const driverBot = useBotNetworkStore((state) => state.driverBot);
  if (item.type === 'bot') {
    return (
      <BotAvatar
        type="assistant"
        size="xs"
        botId={item.botUuid?.split(':')[0]}
        name={item.name}
        avatarUrl={
          item.botUuid === driverBot?.bot_uuid
            ? driverBot?.avatar_url
            : item.avatar
        }
        className="!w-full !h-full !rounded-none"
      />
    );
  }
  return (
    <div className="w-full h-full bg-slate-200 flex items-center justify-center text-[9px] font-medium text-slate-600">
      {(item.name || 'U').charAt(0)}
    </div>
  );
};

interface GroupAvatarGridProps {
  avatars: GridAvatarItem[];
}

const GroupAvatarGrid: React.FC<GroupAvatarGridProps> = ({ avatars }) => {
  const items = avatars.slice(0, 4);
  const count = items.length;

  const base =
    'w-10 h-10 rounded-xl overflow-hidden flex-shrink-0 bg-slate-100';

  if (count === 0) {
    return <div className={base} />;
  }

  if (count === 1) {
    return (
      <div className={base}>
        <Cell item={items[0]} />
      </div>
    );
  }

  if (count === 2) {
    return (
      <div className={`${base} flex gap-px`}>
        {items.map((item, i) => (
          <div key={i} className="flex-1 h-full overflow-hidden">
            <Cell item={item} />
          </div>
        ))}
      </div>
    );
  }

  if (count === 3) {
    return (
      <div className={`${base} flex flex-col gap-px`}>
        <div className="flex flex-1 gap-px">
          {items.slice(0, 2).map((item, i) => (
            <div key={i} className="flex-1 overflow-hidden">
              <Cell item={item} />
            </div>
          ))}
        </div>
        <div className="flex flex-1 justify-center">
          <div className="w-1/2 overflow-hidden">
            <Cell item={items[2]} />
          </div>
        </div>
      </div>
    );
  }

  // 4 人：2×2 四宫格
  return (
    <div className={`${base} grid grid-cols-2 grid-rows-2 gap-px`}>
      {items.map((item, i) => (
        <div key={i} className="overflow-hidden">
          <Cell item={item} />
        </div>
      ))}
    </div>
  );
};

export default GroupAvatarGrid;
