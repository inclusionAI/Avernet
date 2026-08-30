import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
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
}) => (
  <div className="flex min-w-0 flex-wrap items-center gap-3">
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
        <SelectItem value="openclaw">OpenClaw 引擎</SelectItem>
        <SelectItem value="claude_code">Claudecode引擎-原生</SelectItem>
        <SelectItem value="aicoding">Claudecode引擎-AIcoding</SelectItem>
        <SelectItem value="hermes">Hermes</SelectItem>
        <SelectItem value="teclaw">TEClaw</SelectItem>
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
    <Button leftIcon={<Cloud className="size-4" />} onClick={onCreateCloud} className="w-full sm:ml-auto sm:w-auto">
      创建云端 Bot
    </Button>
  </div>
);

export default BotWorkshopToolbar;
