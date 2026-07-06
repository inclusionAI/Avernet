/**
 * BotSwitcher - 移动端 Header 中间 Bot/群聊切换器
 *
 * 功能：
 * 1. 显示当前激活的 Bot 名称
 * 2. 点击展开 Bot/群聊列表
 * 3. 支持切换 Bot
 * 4. 显示 Bot 健康状态（红色提示、禁用点击、tooltip）
 *
 * 样式规范详见 docs/移动端体验规范.md §3.2
 */
import { Drawer, DrawerContent } from '@/components/ui/drawer';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useBot } from '@/hooks/useBot';
// import { useBotHealthPolling } from '@/hooks/useSystem';
// import { useSingleBotHealth } from '@/hooks/useSystem';
import type { Bot } from '@/stores/botStore';
import { cn } from '@/utils/utils';
import { AlertCircle, Bot as BotIcon, Check, Loader2 } from 'lucide-react';
import React, { useState } from 'react';
import { TitleSwitcher } from './index';

/**
 * Bot 健康状态 Badge（临时隐藏）
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function BotHealthBadge({ botId }: { botId: string }) {
  // const { isHealthy, isEngineHealthy, isSandboxHealthy } =
  //   useSingleBotHealth(botId);
  const isHealthy = true;

  if (isHealthy) return null;

  return (
    <span className="flex items-center gap-0.5 text-[10px] text-red-500 bg-red-50 px-1.5 py-0.5 rounded flex-shrink-0">
      <AlertCircle size={9} />
      健康异常
    </span>
  );
}

/**
 * 单个 Bot 列表项组件
 * 抽离出来以便使用 useSingleBotHealth hook
 */
function BotListItem({
  bot,
  isActive,
  isSwitching,
  onClick,
}: {
  bot: Bot;
  isActive: boolean;
  isSwitching: boolean;
  onClick: () => void;
}) {
  // 临时隐藏健康检查，使用默认值
  // const { isHealthy } = useSingleBotHealth(bot.bot_id);
  const isHealthy = true;
  const hasHealthIssue = !isHealthy;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isSwitching || hasHealthIssue}
      data-aspm-click="ca114847.da193993"
      data-aspm-desc="MobileHeader-BotSwitcher-Bot列表项"
      data-aspm-param={``}
      data-aspm-expo
      className={cn(
        'flex items-center gap-3 px-4 py-3.5 text-left transition-colors w-full',
        isActive
          ? 'bg-lavender-50 text-lavender-600'
          : hasHealthIssue
          ? 'bg-red-50/80 text-red-700 hover:bg-red-100'
          : 'text-slate-700 hover:bg-slate-50',
        (isSwitching || hasHealthIssue) && 'opacity-70 cursor-not-allowed',
      )}
    >
      {/* Bot 图标 */}
      <div
        className={cn(
          'w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0',
          hasHealthIssue ? 'bg-red-100' : 'bg-lavender-50',
        )}
      >
        <BotIcon
          size={20}
          className={hasHealthIssue ? 'text-red-500' : 'text-lavender-500'}
        />
      </div>

      {/* Bot 信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={cn(
              'text-sm font-medium truncate flex-shrink min-w-0',
              hasHealthIssue ? 'text-red-700' : 'text-slate-800',
            )}
          >
            {bot.bot_name}
          </span>
          <span className="text-[10px] text-slate-400 truncate">
            {bot.bot_id}
          </span>
          {bot.bot_id === 'default' && (
            <span className="text-[10px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded flex-shrink-0">
              默认
            </span>
          )}
          {/* 健康状态 Badge */}
          <BotHealthBadge botId={bot.bot_id} />
        </div>
        <span className="text-xs text-slate-400 truncate block mt-0.5">
          {bot.active_engine || bot.engine_types?.[0] || 'OpenClaw'}
        </span>
      </div>

      {/* 选中状态或切换中 */}
      {isActive && (
        <Check size={18} className="text-lavender-600 flex-shrink-0" />
      )}
      {isSwitching && (
        <Loader2
          size={18}
          className="animate-spin text-lavender-600 flex-shrink-0"
        />
      )}
    </button>
  );
}

/**
 * Bot 健康状态 Tooltip 包装组件（临时隐藏）
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function BotHealthTooltipWrapper({
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  bot,
  children,
}: {
  bot: Bot;
  children: React.ReactNode;
}) {
  // const {
  //   isHealthy,
  //   isEngineHealthy,
  //   isSandboxHealthy,
  //   engineStateLabel,
  //   engineMessage,
  // } = useSingleBotHealth(bot.bot_id);
  // const hasHealthIssue = !isHealthy;

  // 临时隐藏健康提示
  return <>{children}</>;

  // if (!hasHealthIssue) return <>{children}</>;

  // return (
  //   <Tooltip delayDuration={0}>
  //     <TooltipTrigger asChild>{children}</TooltipTrigger>
  //     <TooltipContent
  //       side="left"
  //       className="bg-white border border-red-200 shadow-lg p-3 max-w-[280px]"
  //     >
  //       <div className="space-y-2">
  //         <p className="text-sm font-medium text-red-600">
  //           {!isSandboxHealthy
  //             ? 'Bot 健康异常'
  //             : engineStateLabel || 'Bot 健康异常'}
  //         </p>
  //         {!isSandboxHealthy && (
  //           <p className="text-xs text-slate-600">
  //             您的Bot异常，推荐进行重启Bot操作
  //           </p>
  //         )}
  //         {!isEngineHealthy && (
  //           <p className="text-xs text-slate-600">
  //             {engineMessage || '您的引擎还在启动中'}
  //           </p>
  //         )}
  //       </div>
  //     </TooltipContent>
  //   </Tooltip>
  // );
}

/**
 * Bot 切换器
 * 用于 MobileHeader 中间显示当前 Bot，点击可切换
 */
export function BotSwitcher() {
  const { bots, activeBot, activeBotId, switchBot } = useBot({
    autoFetchTotalBotCount: false,
  });
  const [open, setOpen] = useState(false);
  const [switchingBotId, setSwitchingBotId] = useState<string | null>(null);

  // 临时隐藏健康检查轮询
  // console.log('[BotSwitcher] bots count:', bots.length);
  // useBotHealthPolling(bots);

  const handleSwitch = async (botId: string) => {
    if (botId === activeBotId) {
      setOpen(false);
      return;
    }

    setSwitchingBotId(botId);
    try {
      const success = await switchBot(botId);
      if (success) {
        setOpen(false);
      }
    } finally {
      setSwitchingBotId(null);
    }
  };

  // 过滤出可切换的 Bot（ACTIVE 状态）
  const switchableBots = bots.filter(
    (bot: { status: string }) => bot.status === 'ACTIVE',
  );

  return (
    <TooltipProvider>
      <>
        {/* Header 中间标题 */}
        <TitleSwitcher
          title={activeBot?.bot_name || '选择 Bot'}
          subtitle={activeBot?.bot_id}
          onClick={() => setOpen(true)}
          showDropdown
          data-aspm-click="ca114848.da194008"
          data-aspm-desc="MobileBotSwitcher-标题切换按钮"
          data-aspm-param={``}
          data-aspm-expo
        />

        {/* Bot 切换抽屉 */}
        <Drawer open={open} onOpenChange={setOpen}>
          <DrawerContent
            position="bottom"
            width="100%"
            height="auto"
            title="切换 Bot"
            overlay
            className="p-0 rounded-t-2xl"
          >
            {/* Bot 列表 */}
            <div className="flex flex-col py-2 max-h-[60vh] overflow-y-auto">
              {switchableBots.length === 0 ? (
                <div className="py-12 text-center text-slate-500 text-sm">
                  暂无可切换的 Bot
                </div>
              ) : (
                switchableBots.map((bot: Bot) => {
                  const isActive = bot.bot_id === activeBotId;
                  const isSwitching = switchingBotId === bot.bot_id;

                  return (
                    <BotHealthTooltipWrapper key={bot.bot_id} bot={bot}>
                      <BotListItem
                        bot={bot}
                        isActive={isActive}
                        isSwitching={isSwitching}
                        onClick={() => handleSwitch(bot.bot_id)}
                        data-aspm-click="ca114848.da194009"
                        data-aspm-desc="MobileBotSwitcher-Bot列表项"
                        data-aspm-param={``}
                        data-aspm-expo
                      />
                    </BotHealthTooltipWrapper>
                  );
                })
              )}
            </div>
          </DrawerContent>
        </Drawer>
      </>
    </TooltipProvider>
  );
}
