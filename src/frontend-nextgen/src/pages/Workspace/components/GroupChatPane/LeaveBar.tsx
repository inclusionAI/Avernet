import { Badge, Button } from '@/components/ui';
import { Loader2, LogOut, Volume2 } from 'lucide-react';

/** 「在会话中隐身」条（human 视角 present 态，显示在聊天输入框上方）。 */
export function LeaveBar({
  humanName,
  onLeave,
  leaving,
}: {
  humanName: string;
  onLeave: () => void;
  leaving: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <div className="flex items-center gap-2.5">
        <Volume2 className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0">
          <p className="text-sm font-medium leading-tight text-foreground">
            {humanName}{' '}
            <Badge tone="success" className="ml-1 align-middle">
              用户发言模式
            </Badge>
          </p>
          <p className="mt-0.5 text-xs leading-tight text-muted-foreground">以用户身份发言中，可随时隐身退出。</p>
        </div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        disabled={leaving}
        onClick={onLeave}
        className="shrink-0 border border-border text-muted-foreground hover:border-destructive/30 hover:text-destructive"
      >
        {leaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
        在会话中隐身
      </Button>
    </div>
  );
}
