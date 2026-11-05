import { Button } from '@/components/ui/Button';
import { RotateCw } from 'lucide-react';

/**
 * 复制进行中的行内重试入口。copy_state 非空时操作列折叠为仅此按钮 ——
 * view/edit/chat/logs/管理菜单一律隐藏，避免对一个尚未收敛的 dst bot 触发
 * 与其复制态冲突的操作；重试安全：create_job 幂等，cas_retry 节流。
 * 开关由调用方 `BOT_COPY_FEATURE_ENABLED` 把守，组件本身不读开关。
 */
function BotCopyRetryAction({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex items-center justify-end" onClick={(event) => event.stopPropagation()}>
      <Button
        variant="ghost"
        size="sm"
        className="gap-1 text-xs text-muted-foreground"
        onClick={(event) => {
          event.stopPropagation();
          onRetry();
        }}
      >
        <RotateCw aria-hidden className="size-3.5" />
        重试
      </Button>
    </div>
  );
}

export default BotCopyRetryAction;
