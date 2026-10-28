// 收起侧栏（56px）的「Bot 泊位」竖轨：整栏收起不留盲区，收起条仍回答
// 「有谁 / 开着哪个」。分组以「界珠」短分隔打结，选中 Bot 亮出
// 泊位缺口（primary 左刻 + 底）——所有图形都承载信息，不做纯装饰。
// 头像是干净的一列：不留任何贯穿线（首版细轨从半透明头像中间穿过，读成故障线，已撤）。
// 好友在线信号点已移除(2026-10-21):metadata status 对已删除 Bot 仍残留 ACTIVE,
// 绿点失真误导;死 Bot 好友关系/元数据同步问题另行立项推动后端修复。
// 交互与展开态同口径：头像直达打开会话（不强迫展开侧栏）；AgentCoding Bot
// 走专用 coding 对话；不可聊 Bot 保持降权占位（与分组「泊位」一致）；
// +N 溢出 / 空位虚线占位各有语义，均请求展开完整侧栏
//（2026-10-14 体验修复：底端不再保留公开 Bot 发现入口）。
import { Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { ConversationBotSection, ConversationBotView } from '@/domain/conversation/types';
import { Plus } from 'lucide-react';
import type { ReactNode } from 'react';
import { AvatarTile } from '../../components/AvatarTile';
import { resolveBotUnavailable } from './ConversationBotUnavailableTooltip';

/** 每分组直达头像上限，超出走「+N」溢出收口（点击展开完整侧栏）。 */
const RAIL_SECTION_MAX_VISIBLE = 5;

export interface ConversationCollapsedRailSection {
  id: ConversationBotSection;
  /** 分组名（我的 Bot / 团队 Bot / 好友 Bot），仅用于 aria 与提示文案。 */
  label: string;
  items: ConversationBotView[];
}

export interface ConversationCollapsedRailProps {
  sections: ConversationCollapsedRailSection[];
  /** 当前选中 Bot（全 Store 单选）；跨组同名 botId 由 selectedSection 消歧。 */
  selectedBotId: string | null;
  selectedSection: ConversationBotSection | null;
  /** 头像直达：打开该 Bot 会话（侧栏保持收起）；调用方负责分组态与懒加载（与泊位 onQuickOpen 同契约）。 */
  onQuickOpen(view: ConversationBotView): void;
  /** 「+N 溢出 / 空位占位」入口：请求展开完整侧栏。 */
  onExpand(): void;
  /** AgentCoding Bot 不建普通会话，直达专用 coding 对话页（与 Bot 行为一致）。 */
  onOpenAgentCodingBot(bot: ConversationBotView['bot']): void;
}

/** 分组界珠：短轨 + 珠点，把「两组之间」编码为轨道上的一个结。 */
function SectionKnot() {
  return (
    <span aria-hidden="true" className="relative my-0.5 flex h-4 w-px justify-center bg-border">
      <span className="absolute top-1/2 h-1 w-1 -translate-y-1/2 rounded-full bg-muted-foreground/60" />
    </span>
  );
}

/** 直达头像：选中 = 泊位缺口（primary 左刻 + bg-selected 底），与分组泊位的选中语义一致。 */
function BotTile({
  view,
  selected,
  tooltip,
  onClick,
}: {
  view: ConversationBotView;
  selected: boolean;
  tooltip: ReactNode;
  onClick(): void;
}) {
  const { bot } = view;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          aria-label={`打开 ${bot.displayName}`}
          aria-current={selected ? 'true' : undefined}
          className="relative h-9 w-9 rounded-full hover:bg-muted"
          onClick={onClick}
        >
          <AvatarTile
            src={bot.avatarUrl}
            label={bot.displayName}
            className="h-7 w-7 rounded-full"
            fallbackContent={
              <span className="text-[10px] font-semibold leading-none tracking-[0.06em]">
                {bot.displayName.slice(0, 1)}
              </span>
            }
          />
          {selected && (
            <span aria-hidden="true" className="absolute -left-1 top-1/2 h-3.5 w-1 -translate-y-1/2 rounded-full bg-primary" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">{tooltip}</TooltipContent>
    </Tooltip>
  );
}

/** 降权占位：不可聊 Bot 不提供直达按钮（cursor 语义与分组泊位一致），仍保留身份提示。 */
function NonInteractiveTile({ view }: { view: ConversationBotView }) {
  const { reason } = resolveBotUnavailable(view.bot, view.section);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="relative cursor-not-allowed rounded-full opacity-45" aria-label={`${view.bot.displayName}（不可发起会话）`}>
          <AvatarTile
            src={view.bot.avatarUrl}
            label={view.bot.displayName}
            className="h-9 w-9 rounded-full"
            fallbackContent={
              <span className="text-[10px] font-semibold leading-none tracking-[0.06em]">
                {view.bot.displayName.slice(0, 1)}
              </span>
            }
          />
        </span>
      </TooltipTrigger>
      <TooltipContent side="right">{reason ?? view.bot.displayName}</TooltipContent>
    </Tooltip>
  );
}

/** 空位虚线占位：分组无成员时仍留一个「可泊」缺口，点击展开侧栏走分组空态引导。 */
function EmptyTile({ label, onExpand }: { label: string; onExpand(): void }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          aria-label={`展开侧栏，添加${label}`}
          className="relative h-9 w-9 rounded-full border border-dashed border-border text-muted-foreground hover:bg-muted"
          onClick={onExpand}
        >
          <Plus className="h-4 w-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">暂无成员，展开添加</TooltipContent>
    </Tooltip>
  );
}

export function ConversationCollapsedRail({
  sections,
  selectedBotId,
  selectedSection,
  onQuickOpen,
  onExpand,
  onOpenAgentCodingBot,
}: ConversationCollapsedRailProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <div
        role="group"
        aria-label="Bot 快捷泊位"
        className="app-scrollbar flex h-full w-full flex-col items-center overflow-y-auto px-1 py-3"
      >
        {sections.map((section, index) => {
          const visible = section.items.slice(0, RAIL_SECTION_MAX_VISIBLE);
          const overflow = section.items.length - visible.length;
          return (
            <div key={section.id} aria-label={section.label} className="flex w-full flex-col items-center">
              {/* 分组间以界珠分隔；组内头像列保持干净——不画贯穿线（避免穿过半透明头像）。 */}
              {index > 0 && <SectionKnot />}
              <div className="relative flex w-full flex-col items-center gap-1 py-1">
                {visible.length === 0 ? (
                  <EmptyTile label={section.label} onExpand={onExpand} />
                ) : (
                  visible.map((view) => {
                    const selected =
                      view.bot.botId === selectedBotId && section.id === selectedSection;
                    // 与 Bot 行同口径：不可用 Bot 不允许通过 Coding 专用入口绕过禁用。
                    if (resolveBotUnavailable(view.bot, view.section).unavailable) {
                      return <NonInteractiveTile key={view.bot.botId} view={view} />;
                    }
                    // AgentCoding 直达 coding 对话；不可聊不提供直达。
                    if (view.bot.isAgentCodingBot) {
                      return (
                        <BotTile
                          key={view.bot.botId}
                          view={view}
                          selected={selected}
                          tooltip={view.bot.displayName}
                          onClick={() => onOpenAgentCodingBot(view.bot)}
                        />
                      );
                    }
                    if (!view.bot.chatable) return <NonInteractiveTile key={view.bot.botId} view={view} />;
                    return (
                      <BotTile
                        key={view.bot.botId}
                        view={view}
                        selected={selected}
                        tooltip={
                          view.section === 'friend' && view.bot.online === true
                            ? `${view.bot.displayName} · 在线`
                            : view.bot.displayName
                        }
                        onClick={() => onQuickOpen(view)}
                      />
                    );
                  })
                )}
                {overflow > 0 && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        aria-label={`展开侧栏，查看${section.label}其余 ${overflow} 个`}
                        className="relative h-7 w-9 rounded-full bg-muted/50 text-[10px] font-normal text-content-soft hover:bg-muted"
                        onClick={onExpand}
                      >
                        +{overflow}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="right">{`还有 ${overflow} 个${section.label}`}</TooltipContent>
                  </Tooltip>
                )}
              </div>
            </div>
          );
        })}
        </div>
    </TooltipProvider>
  );
}
