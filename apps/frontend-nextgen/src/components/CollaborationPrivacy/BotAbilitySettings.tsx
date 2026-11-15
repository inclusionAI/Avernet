/**
 * 协作能力分区：四个开关行。
 * 从 settingsSections 拆分（文件行数门禁），供协作权限页与 BotWorkshop「公开设置」弹窗共用；
 * 内部公开设置通过 afterCollaborationStatus 在首行后插入修复行。
 */
import type { CollaborationBot } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting } from '@/services/collaborationPrivacy';
import type { ReactNode } from 'react';
import { SettingRow } from './SettingRow';

export interface BotAbilitySettingsProps {
  bot: CollaborationBot;
  busyAction: string | null | undefined;
  onToggleDirect: (bot: CollaborationBot, setting: DirectSetting, value: boolean | 'online' | 'hidden') => void;
  /** 通用配置弹窗两列网格布局；缺省为页面列表布局（divide-y）。 */
  grid?: boolean;
  /** 仅展示指定能力项；通用配置弹窗按入口规则裁剪，协作权限页缺省展示全部。 */
  visibleSettings?: readonly DirectSetting[];
  /** 内部公开设置首行（参与协作群聊）之后追加的自定义行（修复入口）；其他调用不传。 */
  afterCollaborationStatus?: ReactNode;
}

export function BotAbilitySettings({
  bot,
  busyAction,
  onToggleDirect,
  grid = false,
  visibleSettings,
  afterCollaborationStatus,
}: BotAbilitySettingsProps) {
  const directBusy = (setting: DirectSetting) => busyAction === `${bot.id}:${setting}`;
  const shouldShow = (setting: DirectSetting) => !visibleSettings || visibleSettings.includes(setting);
  const rows = (
    <>
      {shouldShow('collaborationStatus') && (
        <>
          <SettingRow
            label="参与协作群聊"
            description="控制当前 Bot 是否可参与群聊。关闭后无法加入新协作群，已加入的协作群也不再回复。"
            checked={bot.collaborationStatus === 'online'}
            disabled={!bot.joinedBcn || bot.collaborationStatus === 'offline'}
            busy={directBusy('collaborationStatus')}
            onChange={(checked) => onToggleDirect(bot, 'collaborationStatus', checked ? 'online' : 'hidden')}
            paddingClassName={grid ? 'py-3' : undefined}
          />
          {afterCollaborationStatus}
        </>
      )}
      {shouldShow('profilePublic') && (
        <SettingRow
          label="公开 Bot 画像"
          description="允许其他用户在群聊中通过「融合模式」查看公开画像并进行跨 Bot 增量洞察。"
          checked={bot.profilePublic}
          disabled={!bot.joinedBcn || bot.profilePublicStatus === 'unavailable'}
          busy={directBusy('profilePublic')}
          status={bot.profilePublicStatus === 'unavailable' ? '暂不可用' : undefined}
          statusReason={
            bot.profilePublicStatus === 'unavailable'
              ? '该 Bot 尚未对其他 Bot 开放可见，请先调整 Bot 可见性'
              : undefined
          }
          onChange={(checked) => onToggleDirect(bot, 'profilePublic', checked)}
          paddingClassName={grid ? 'py-3' : undefined}
        />
      )}
      {shouldShow('taskClaimingEnabled') && (
        <SettingRow
          label="任务认领"
          description="开启后，Bot 将每天自动扫描任务广场并认领可执行的任务。"
          checked={bot.taskClaimingEnabled}
          disabled={!bot.joinedBcn}
          busy={directBusy('taskClaimingEnabled')}
          onChange={(checked) => onToggleDirect(bot, 'taskClaimingEnabled', checked)}
          paddingClassName={grid ? 'py-3' : undefined}
        />
      )}
      {shouldShow('dreamModelEnabled') && (
        <SettingRow
          label="Dream Mode"
          description="开启后，Bot 将每天基于用户数据（语雀、会议纪要等）挖掘潜在任务并推送。"
          checked={bot.dreamModelEnabled}
          disabled={!bot.joinedBcn}
          busy={directBusy('dreamModelEnabled')}
          onChange={(checked) => onToggleDirect(bot, 'dreamModelEnabled', checked)}
          paddingClassName={grid ? 'py-3' : undefined}
        />
      )}
    </>
  );
  return (
    <>
      <div className="mb-3 flex items-center">
        <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">协作能力</h4>
      </div>
      {grid ? (
        <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">{rows}</div>
      ) : (
        <div className="divide-y divide-divider">{rows}</div>
      )}
    </>
  );
}
