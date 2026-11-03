import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { useBotEngineOptions } from '@/hooks/useBotEngineOptions';
import type { BotDeployment, BotServiceMode } from '@/services/botWorkshop';
import { Cloud, Plus, Search } from 'lucide-react';
import React from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

interface BotWorkshopToolbarProps {
  keyword: string;
  engine: string;
  deployment?: BotDeployment;
  serviceMode?: BotServiceMode;
  onKeywordChange: (value: string) => void;
  onEngineChange: (value: string) => void;
  onDeploymentChange: (value?: BotDeployment) => void;
  onServiceModeChange: (value?: BotServiceMode) => void;
  onCreateCloud: () => void;
  onCreateLocal?: () => void;
  localCreateDisabledReason?: string;
  onReset: () => void;
}

const BotWorkshopToolbar: React.FC<BotWorkshopToolbarProps> = ({
  keyword,
  engine,
  deployment,
  serviceMode,
  onKeywordChange,
  onEngineChange,
  onDeploymentChange,
  onServiceModeChange,
  onCreateCloud,
  onCreateLocal,
  localCreateDisabledReason,
  onReset,
}) => {
  // 引擎选项经 capability 解析注入（Open Core 仅 openclaw；internal overlay 保留 AgentCoding 及其他既有引擎），
  // 组件不硬编码清单；「全部」哨兵值留在筛选器本层，不入 capability。
  //
  // 筛选器名作为常驻标题放在 Select 左侧，框内只反映当前选择（未筛选即「全部」）。
  // 故 value 用哨兵值 'all' 而非 undefined：这样下拉首项「全部」呈现选中态，框内文案
  // 与之一致。三个框靠左侧标题区分，不靠框内文案——去掉标题就会退化成三个「全部」。
  const engineOptions = useBotEngineOptions();
  const [createOpen, setCreateOpen] = React.useState(false);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3">
      <div className="relative w-full sm:w-[240px] sm:shrink-0">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={keyword}
          onChange={(event) => onKeywordChange(event.target.value)}
          placeholder="搜索 Bot 名称"
          className="pl-9"
          aria-label="搜索 Bot"
        />
      </div>
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-filter-engine-label" className="shrink-0 text-xs font-medium text-muted-foreground">
          引擎类型
        </span>
        <Select value={engine || 'all'} onValueChange={(value) => onEngineChange(value === 'all' ? '' : value)}>
          <SelectTrigger className="w-full sm:w-[132px]" aria-labelledby="bot-filter-engine-label">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            {engineOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-filter-deployment-label" className="shrink-0 text-xs font-medium text-muted-foreground">
          部署方式
        </span>
        <Select
          value={deployment ?? 'all'}
          onValueChange={(value) => onDeploymentChange(value === 'all' ? undefined : (value as BotDeployment))}
        >
          <SelectTrigger className="w-full sm:w-[132px]" aria-labelledby="bot-filter-deployment-label">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="local">本地</SelectItem>
            <SelectItem value="cloud">云端</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-filter-service-label" className="shrink-0 text-xs font-medium text-muted-foreground">
          服务类型
        </span>
        <Select
          value={serviceMode ?? 'all'}
          onValueChange={(value) => onServiceModeChange(value === 'all' ? undefined : (value as BotServiceMode))}
        >
          <SelectTrigger className="w-full sm:w-[132px]" aria-labelledby="bot-filter-service-label">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="service">服务化</SelectItem>
            <SelectItem value="non-service">非服务化</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="ml-auto flex w-full items-center justify-end gap-3 sm:w-auto">
        {keyword || engine || deployment || serviceMode ? (
          <Button variant="ghost" size="sm" onClick={onReset}>
            重置
          </Button>
        ) : null}
        {/* 创建入口合并为「创建Bot」主按钮（dmore 稿 artboard-059）：点击弹出气泡选择云端/本地。
          item 悬停底色按稿取 --selected（#EBEBEB 胶囊）；本地不可创建时置灰并悬停展示原因。 */}
        {onCreateLocal ? (
          <Popover
            open={createOpen}
            onOpenChange={(next) => {
              setCreateOpen(next);
              if (next) Tracert.click(Points.botWorkshop.createEntry);
            }}
          >
            <PopoverTrigger asChild>
              <Button className="w-full sm:w-auto" leftIcon={<Plus aria-hidden className="size-3.5" />}>
                创建Bot
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-32 rounded-lg p-1.5" aria-label="选择 Bot 创建方式">
              <div className="space-y-1" role="menu" aria-label="Bot 创建方式">
                <Button
                  variant="ghost"
                  size="sm"
                  role="menuitem"
                  className="w-full justify-start px-3 hover:bg-selected"
                  onClick={() => {
                    setCreateOpen(false);
                    onCreateCloud();
                  }}
                >
                  创建云端Bot
                </Button>
                {localCreateDisabledReason ? (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="block" tabIndex={0} aria-describedby="local-create-disabled-reason">
                          <Button variant="ghost" size="sm" role="menuitem" disabled className="w-full justify-start px-3">
                            创建本地Bot
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent id="local-create-disabled-reason">{localCreateDisabledReason}</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    role="menuitem"
                    className="w-full justify-start px-3 hover:bg-selected"
                    onClick={() => {
                      setCreateOpen(false);
                      onCreateLocal();
                    }}
                  >
                    创建本地Bot
                  </Button>
                )}
              </div>
            </PopoverContent>
          </Popover>
        ) : (
          <Button
            leftIcon={<Cloud aria-hidden className="size-4" />}
            onClick={onCreateCloud}
            className="w-full sm:w-auto"
          >
            创建云端 Bot
          </Button>
        )}
      </div>
    </div>
  );
};

export default BotWorkshopToolbar;