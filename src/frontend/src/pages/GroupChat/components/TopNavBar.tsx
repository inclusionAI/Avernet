/**
 * TopNavBar - 顶部导航栏
 *
 * 包含 Bot Tab 列表，每个 Tab 支持可见性切换
 * 内部集成 useBotNetwork hook，无需外部传入切换逻辑
 */

import { useExt } from '@/capabilities';
import * as BcnController from '@/services/backend-api/BcnController';
import * as BotController from '@/services/backend-api/BotController';
import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { AppExt } from '@/shell';
import { useBotStore } from '@/stores/botStore';
import type { BotNetworkInfo } from '@/stores/botNetworkStore';
import { useUserStore } from '@/stores/userStore';
import { resolveBotOwnerId } from '@/utils/activeBotContext';
import { Loader2, Network, Plus } from 'lucide-react';
import React, { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { useBotNetwork } from '../hooks/useBotNetwork';
import type { BotTabItem } from '../types';
import AddBotGuideModal from './AddBotGuideModal';
import BotTab from './BotTab';

interface TopNavBarProps {
  /** Bot 列表（已合并BCN状态） */
  myBots: BotNetworkInfo[];
  /** 原始Bot数据源（用于重新初始化） */
  botsToUse: BotTabItem[];
  /** 当前选中的 Bot UUID */
  selectedBotUuid: string | null;
  /** 是否正在加载 Bot 列表 */
  isLoadingMyBots: boolean;
  /** Bot 切换回调 */
  onBotSwitch: (botUuid: string) => void;
}

const TopNavBar: React.FC<TopNavBarProps> = ({
  myBots,
  botsToUse,
  selectedBotUuid,
  isLoadingMyBots,
  onBotSwitch,
}) => {
  // 获取用户展示信息（用于 human actor）；由 useHumanIdentity 写入 userStore，
  // 身份来源差异（开源 /auth/user、内部 __TERN__）已收口到 authAdapter。
  const userId = useUserStore((state) => state.userId);
  const userNickName = useUserStore((state) => state.nickName);
  const userAvatarUrl = useUserStore((state) => state.avatarUrl);
  const userDisplayName = userNickName || userId || undefined;

  // 创建 Bot 接入引导入口（bcnBotOnboarding）：开源专属，开源默认 true，内部 extend 为 false。
  const { bcnBotOnboarding } = useExt(AppExt).features;
  // 高级设置（内部专属，代码不可见）：组件经 AppExt.slots.advancedSettings 注入，开源默认 null（不渲染）。
  const { advancedSettings: AdvancedSettingsSlot } = useExt(AppExt).slots;

  // +新Bot 接入引导弹窗
  const [addBotOpen, setAddBotOpen] = useState(false);

  // 内部状态
  const [togglingVisibility, setTogglingVisibility] = useState<string | null>(
    null,
  );
  const [onboardingBotUuid, setOnboardingBotUuid] = useState<string | null>(
    null,
  );
  // 刷新名称状态：记录正在刷新的 bot_uuid 和是否在等待中
  const [refreshingBotUuid, setRefreshingBotUuid] = useState<string | null>(
    null,
  );

  // 内部获取方法
  const {
    setBotVisibility,
    initUnifiedBotTabs,
    refreshBotName,
    initHumanActor,
  } = useBotNetwork();

  // 初始化 Human 状态
  const [initingHumanUuid, setInitingHumanUuid] = useState<string | null>(null);

  // 内部处理可见性设置
  const handleSetVisibility = async (botUuid: string, visibility: any) => {
    const bot = myBots.find((b) => b.bot_uuid === botUuid);
    if (!bot || bot.visibility === visibility) return;
    setTogglingVisibility(botUuid);
    try {
      await setBotVisibility(botUuid, visibility);
    } finally {
      setTogglingVisibility(null);
    }
  };

  // 内部处理 Bot 入网
  const handleOnboard = async (botUuid: string, onboard: boolean) => {
    if (!onboard) return; // 暂不支持离网
    const bot = myBots.find((b) => b.bot_uuid === botUuid);
    if (!bot || bot.visibility !== 'offline') return;

    setOnboardingBotUuid(botUuid);
    try {
      // 调用 BCN 入网接口
      const response = await BcnController.onboardBot({
        bot_id: bot.bot_uuid, // 使用 bot_id:entity_id 格式
        name: bot.bot_name,
        summary: bot.summary || '',
        hidden: true,
      });

      // 检查是否入网失败
      if (response.data?.onboarded === false) {
        const errorMsg = response.message || 'Bot 入网失败';
        const [botId] = bot.bot_uuid.split(':');
        // 查询完整 Bot 记录判断是否 teclaw 引擎
        const fullBot = useBotStore.getState().getBotById(botId);
        const isTeclaw = fullBot?.active_engine === ENGINE_TYPE.TECLAW;

        if (isTeclaw) {
          // teclaw 不支持重启（会销毁容器且无法重建），引导用户更新配置或联系运维
          toast.error('入网失败，可尝试更新配置后重试。如问题持续请联系运维。');
        } else {
          // 非 teclaw：保留原有重启入口
          toast.error(errorMsg, {
            action: {
              label: '立即重启',
              onClick: async () => {
                try {
                  await BotController.restartBot({
                    bot_id: botId,
                    user_id: '',
                    owner_id:
                      resolveBotOwnerId(botId) || useUserStore.getState().userId,
                  });
                  toast.success('Bot 重启中，请稍后重试入网');
                } catch (restartError) {
                  console.error(
                    '[TopNavBar] Failed to restart bot:',
                    restartError,
                  );
                  toast.error('Bot 重启失败');
                }
              },
            },
          });
        }
        return;
      }

      if (response.error) {
        throw new Error(response.error);
      }

      await BcnController.setBotVisibility({
        bot_uuid: botUuid,
        visibility: 'protected', // 默认入网为受保护模式
      });

      // 重新初始化 Bot 列表
      await initUnifiedBotTabs({
        localBots: botsToUse,
        targetBotUuid: botUuid,
      });
      toast.success('Bot 入网成功');
    } catch (error) {
      console.error('[TopNavBar] Failed to onboard bot:', error);
      toast.error('Bot 入网失败:' + error);
    } finally {
      setOnboardingBotUuid(null);
    }
  };

  // 内部处理刷新名称
  const handleRefreshName = async (botUuid: string): Promise<void> => {
    const bot = myBots.find((b) => b.bot_uuid === botUuid);
    if (!bot || !bot.externalName) {
      toast.error('Bot 信息不完整');
      return;
    }

    setRefreshingBotUuid(botUuid);
    try {
      const success = await refreshBotName(
        botUuid,
        bot.externalName,
        bot.summary,
      );
      if (success) {
        toast.success('名称同步成功');
      }
    } finally {
      setRefreshingBotUuid(null);
    }
  };

  // 内部处理初始化 Human Actor
  const handleInitHuman = async (botUuid: string): Promise<void> => {
    const bot = myBots.find((b) => b.bot_uuid === botUuid);
    if (!bot || bot.actor_kind !== 'human') {
      toast.error('Human Actor 信息不完整');
      return;
    }

    setInitingHumanUuid(botUuid);
    try {
      const success = await initHumanActor(botUuid);
      if (success) {
        // 重新初始化 Bot 列表以获取最新状态
        await initUnifiedBotTabs({ localBots: botsToUse });
      }
    } finally {
      setInitingHumanUuid(null);
    }
  };

  const handleSelfRepairComplete = useCallback(async () => {
    await initUnifiedBotTabs({ localBots: botsToUse });
  }, [botsToUse, initUnifiedBotTabs]);

  const handleActiveOnlyChange = useCallback(
    async (activeOnly: boolean) => {
      await initUnifiedBotTabs({
        localBots: botsToUse,
        targetBotUuid: selectedBotUuid || undefined,
        activeOnly,
      });
    },
    [initUnifiedBotTabs, botsToUse, selectedBotUuid],
  );

  return (
    <div className="flex h-[52px] items-center bg-[#f1f5f9]">
      {/* Bot Tab 列表 */}
      <div className="flex h-full w-full flex-1 items-end gap-1 overflow-x-auto overflow-y-hidden">
        {isLoadingMyBots && myBots.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-1.5">
            <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
            <span className="text-sm text-slate-400">加载中...</span>
          </div>
        ) : myBots.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-1.5 text-slate-400">
            <Network className="w-4 h-4" />
            <span className="text-sm">暂无 Bot</span>
          </div>
        ) : (
          <>
            {myBots.map((bot) => (
              <BotTab
                key={bot.bot_uuid}
                bot={bot}
                isSelected={bot.bot_uuid === selectedBotUuid}
                isTogglingVisibility={togglingVisibility === bot.bot_uuid}
                onSetVisibility={handleSetVisibility}
                onBotSwitch={onBotSwitch}
                onOnboard={handleOnboard}
                isOnboarding={onboardingBotUuid === bot.bot_uuid}
                onRefreshName={handleRefreshName}
                isRefreshingName={refreshingBotUuid === bot.bot_uuid}
                userDisplayName={
                  bot.actor_kind === 'human' ? userDisplayName : undefined
                }
                userAvatarUrl={
                  bot.actor_kind === 'human' ? userAvatarUrl : undefined
                }
                onInitHuman={handleInitHuman}
                isInitingHuman={initingHumanUuid === bot.bot_uuid}
              />
            ))}
            {/* Tab 末尾：创建 Bot 接入引导入口（开源专属，bcnBotOnboarding 门控） */}
            {bcnBotOnboarding && (
              <button
                type="button"
                className="flex h-[52px] shrink-0 items-center gap-1.5 rounded-t-lg border border-b-0 border-[#d1d5db] bg-[#edf0f5] px-4 text-[13px] font-semibold text-gray-500 transition-colors hover:bg-[#e2e8f0] hover:text-gray-600"
                onClick={() => setAddBotOpen(true)}
              >
                <Plus className="h-4 w-4" />
                <span>接入 Bot</span>
              </button>
            )}
          </>
        )}
      </div>
      <div className="flex h-full shrink-0 items-center gap-1 px-2">
        {AdvancedSettingsSlot && (
          <AdvancedSettingsSlot
            onActiveOnlyChange={handleActiveOnlyChange}
            onRepairComplete={handleSelfRepairComplete}
          />
        )}
      </div>
      {bcnBotOnboarding && (
        <AddBotGuideModal open={addBotOpen} onOpenChange={setAddBotOpen} />
      )}
    </div>
  );
};

export default TopNavBar;
