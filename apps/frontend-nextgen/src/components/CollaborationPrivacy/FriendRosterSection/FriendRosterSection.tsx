/**
 * 公开设置「好友名单」小节（Bot 好友名单 PRD v1.3 R1/R3）：
 * 位于「好友审批策略」小节正下方，常驻显示；摘要行给数量分计（好友用户 / 好友 Bot），
 * 「管理好友」入口打开右侧管理抽屉。仅 Owner 视角由父级 gate（R7/AC-07）。
 */
import { Button } from '@/components/ui/Button';
import { countRoster } from '@/domain/collaborationPrivacy/friendRoster';
import { useBotFriendRoster } from '@/hooks/useBotFriendRoster';
import { useMemo, useState } from 'react';
import { LabelHelpTooltip } from '../HelpTooltip';
import { FriendRosterDrawer } from './FriendRosterDrawer';

export interface FriendRosterSectionProps {
  /** 协作域 Bot UUID（好友连接查询的 actor_id）。 */
  botId: string;
  /** Bot 名称（抽屉标题「{Bot 名}的好友名单」）。 */
  botName: string;
  /** 仅 Owner 为 true（父级按 bot.ownerId === 登录人 gate）。 */
  enabled: boolean;
}

export function FriendRosterSection({ botId, botName, enabled }: FriendRosterSectionProps) {
  const roster = useBotFriendRoster(botId, enabled);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const counts = useMemo(() => countRoster(roster.friends), [roster.friends]);

  const summary = roster.loading
    ? '加载中…'
    : counts.total > 0
      ? `${counts.total} 位好友（${counts.human} 用户 / ${counts.bot} Bot）`
      : '暂无好友';

  return (
    <>
      <div className="mb-3 flex items-center gap-1.5">
        <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">好友名单</h4>
        <LabelHelpTooltip
          label="好友名单"
          content="查看并管理当前添加了该 Bot 的好友用户与好友 Bot；此处移除将与既有好友动线操作同一份关系数据。"
        />
      </div>
      <div className="flex items-center justify-between gap-4 py-1">
        <p className="m-0 text-sm text-foreground">{summary}</p>
        <Button variant="secondary" onClick={() => setDrawerOpen(true)} disabled={!enabled}>
          管理好友
        </Button>
      </div>
      <FriendRosterDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        botName={botName}
        friends={roster.friends}
        loading={roster.loading}
        error={roster.error}
        onRetry={roster.reload}
        onRemove={roster.removeFriend}
      />
    </>
  );
}