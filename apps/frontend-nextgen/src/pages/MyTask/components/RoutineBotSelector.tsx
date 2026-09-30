import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';

export interface RoutineBotOption {
  value: string;
  label: string;
}

interface RoutineBotSelectorProps {
  options: RoutineBotOption[];
  value: string;
  onChange: (botId: string) => void;
}

export function RoutineBotSelector({ options, value, onChange }: RoutineBotSelectorProps) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="shrink-0 text-xs font-medium text-muted-foreground">Bot</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="w-full text-xs">
          <SelectValue placeholder="请选择 Bot" />
        </SelectTrigger>
        <SelectContent>
          {options.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
