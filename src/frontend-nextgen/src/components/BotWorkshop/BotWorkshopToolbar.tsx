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
      <Select value={engine || 'all'} onValueChange={(value) => onEngineChange(value === 'all' ? '' : value)}>
        <SelectTrigger className="w-full sm:w-[150px]" aria-label="引擎类型">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">引擎类型</SelectItem>
          {engineOptions.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={deployment ?? 'all'}
        onValueChange={(value) => onDeploymentChange(value === 'all' ? undefined : (value as BotDeployment))}
      >
        <SelectTrigger className="w-full sm:w-[110px]" aria-label="部署方式">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">部署方式</SelectItem>
          <SelectItem value="local">本地</SelectItem>
          <SelectItem value="cloud">云端</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={serviceMode ?? 'all'}
        onValueChange={(value) => onServiceModeChange(value === 'all' ? undefined : (value as BotServiceMode))}
      >
        <SelectTrigger className="w-full sm:w-[120px]" aria-label="服务类型">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">服务类型</SelectItem>
          <SelectItem value="service">服务化</SelectItem>
          <SelectItem value="non-service">非服务化</SelectItem>
        </SelectContent>
      </Select>
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
