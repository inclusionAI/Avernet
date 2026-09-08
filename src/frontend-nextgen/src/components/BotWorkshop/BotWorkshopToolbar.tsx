import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { useBotEngineOptions } from '@/hooks/useBotEngineOptions';
import type { BotDeployment, BotServiceMode } from '@/services/botWorkshop';
import { Cloud, Search } from 'lucide-react';
import React from 'react';

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
  total?: number;
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
  total,
  onReset,
}) => {
  // 引擎选项经 capability 解析注入（Open Core 仅 openclaw；internal overlay 保留 AgentCoding 及其他既有引擎），
  // 组件不硬编码清单；「全部」哨兵值留在筛选器本层，不入 capability。
  //
  // 筛选器名作为常驻标题放在 Select 左侧，框内只反映当前选择（未筛选即「全部」）。
  // 故 value 用哨兵值 'all' 而非 undefined：这样下拉首项「全部」呈现选中态，框内文案
  // 与之一致。三个框靠左侧标题区分，不靠框内文案——去掉标题就会退化成三个「全部」。
  const engineOptions = useBotEngineOptions();
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-4 px-3">
      <div className="relative w-full sm:w-[200px] sm:shrink-0">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          value={keyword}
          onChange={(event) => onKeywordChange(event.target.value)}
          placeholder="搜索 Bot..."
          className="pl-9"
          aria-label="搜索 Bot"
        />
      </div>
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span id="bot-filter-engine-label" className="shrink-0 text-xs font-medium text-muted-foreground">
          引擎类型
        </span>
        <Select value={engine || 'all'} onValueChange={(value) => onEngineChange(value === 'all' ? '' : value)}>
          <SelectTrigger className="w-full sm:w-[150px]" aria-labelledby="bot-filter-engine-label">
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
          <SelectTrigger className="w-full sm:w-[110px]" aria-labelledby="bot-filter-deployment-label">
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
          <SelectTrigger className="w-full sm:w-[120px]" aria-labelledby="bot-filter-service-label">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="service">服务化</SelectItem>
            <SelectItem value="non-service">非服务化</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="ml-auto flex w-full items-center justify-end gap-4 sm:w-auto">
        <div className="flex items-center gap-2">
          {keyword || engine || deployment || serviceMode ? (
            <Button variant="ghost" size="sm" onClick={onReset}>
              重置
            </Button>
          ) : null}
          {total !== undefined ? (
            <span className="text-xs tabular-nums text-muted-foreground">共 {total} 条</span>
          ) : null}
        </div>
        <Button leftIcon={<Cloud className="size-4" />} onClick={onCreateCloud} className="w-full sm:w-auto">
          创建云端 Bot
        </Button>
      </div>
    </div>
  );
};

export default BotWorkshopToolbar;
