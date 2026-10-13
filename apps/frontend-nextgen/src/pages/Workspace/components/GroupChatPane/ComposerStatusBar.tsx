import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { MessageViewScope } from '@/domain/collaboration/types';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { Eye, EyeOff, Loader2, LogOut, RefreshCw } from 'lucide-react';
import { useState } from 'react';

/**
 * Composer 状态帽条（dmore artboard-017 × 025 交叉实测，design 决策 7/8 与附录 B）：
 * 紧贴输入卡片上缘的 muted 窄条（仅上圆角），是发言模式与连接状态的唯一载体——
 * - connected：dot #09090B + 「用户发言模式 ｜ 当前将以用户身份发送到会话」，右侧 icon + 「退出会话」；
 * - 异常态：左侧整体变形为状态 dot + 状态名 + 说明文案；失败/断开在「退出会话」之左加「重新连接」；
 * - 断连各态输入卡片由 GroupChatComposer 以灰底呈现不可输入（宿主传 connectionStatus）。
 * 视角切换（参与者 ↔ 完整）设计稿无此钮、功能不得删——收为条内 icon 钮 + tooltip（受控偏离）。
 * Bot 视角与未加入态不渲染本条（宿主控制）；退出/切换/重连沿用既有 service 调用与 loading 态。
 */
const ABNORMAL_COPY: Record<'connecting' | 'reconnecting' | 'error' | 'disconnected', string> = {
  connecting: '正在连接并加载会话与消息，请稍候…',
  reconnecting: '正在重新连接并加载消息，请稍候…',
  error: '暂时无法连接，点击「重新连接」重试',
  disconnected: '会话连接已断开，请重新连接后继续对话',
};

const STATUS_LABEL: Record<'connecting' | 'reconnecting' | 'error' | 'disconnected', string> = {
  connecting: '连接中',
  reconnecting: '重连中',
  error: '连接失败',
  disconnected: '已断开',
};

/** dot 4 色 5 态（附录 B）：connected 黑 / 连接中·重连中 蓝 / 失败 红 / 断开 橙（#FE9000 字面值待走查）。 */
function dotClass(status: ProviderConnectionStatus): string {
  switch (status) {
    case 'connected':
      return 'bg-foreground';
    case 'connecting':
    case 'reconnecting':
      return 'bg-primary';
    case 'error':
      return 'bg-destructive';
    default:
      return 'bg-[#FE9000]';
  }
}

export interface ComposerStatusBarProps {
  connectionStatus: ProviderConnectionStatus;
  humanName: string;
  /** human 成员消息可见域回显；缺失（null/undefined）时不渲染视角切换按钮。 */
  viewScope?: MessageViewScope | null;
  switchingViewScope?: boolean;
  onViewScopeChange?: (scope: MessageViewScope) => void;
  /** 退出会话（原「在会话中隐身」）；沿用既有 leaveSession 调用（返回 boolean 由本组件吞掉）。 */
  onLeave: () => Promise<unknown> | void;
  onReconnect: () => void;
}

export function ComposerStatusBar({
  connectionStatus,
  humanName,
  viewScope,
  switchingViewScope,
  onViewScopeChange,
  onLeave,
  onReconnect,
}: ComposerStatusBarProps) {
  const [leaving, setLeaving] = useState(false);
  const connected = connectionStatus === 'connected';
  const abnormal = connected ? null : ABNORMAL_COPY[connectionStatus] ?? null;
  const actionable = connectionStatus === 'error' || connectionStatus === 'disconnected';
  const scopeLabel = viewScope === 'full' ? '切换到参与者视角' : '切换到完整视角';

  const handleLeave = () => {
    setLeaving(true);
    void Promise.resolve(onLeave()).finally(() => setLeaving(false));
  };

  return (
    // role=status：连接态切换时向读屏播报（live region）；aria-label 随形态变化，
    // 异常态不得仍自称「用户发言模式」。
    // 宽度：mx-auto + 每侧 32px 让位（总内缩 64px）——较帧值 26px 更明显的「帽章节」短一截
    // 观感（走查反馈 2026-10-13，受控偏离登记 design 附录 B）；色彩一律走 token，禁 zinc 硬编码。
    <div
      role="status"
      aria-label={`${humanName}${connected ? ' 用户发言模式' : ` ${STATUS_LABEL[connectionStatus]}`}`}
      className="mx-auto flex h-[26px] w-[calc(100%-64px)] items-center justify-between rounded-t-xl bg-muted-foreground/10 pl-3 pr-1.5"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span aria-hidden className={`size-1.5 shrink-0 rounded-full ${dotClass(connectionStatus)}`} />
        <span className="shrink-0 text-xs font-medium leading-4 text-foreground">
          {connected ? '用户发言模式' : STATUS_LABEL[connectionStatus]}
        </span>
        <span className="min-w-0 truncate text-xs leading-4 text-muted-foreground">
          {connected ? '当前将以用户身份发送到会话' : abnormal}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1 text-muted-foreground">
        {actionable ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onReconnect}
            className="h-6 shrink-0 gap-1 rounded-lg px-1.5 text-xs hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            重新连接
          </Button>
        ) : null}
        {viewScope && onViewScopeChange ? (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={scopeLabel}
                  disabled={switchingViewScope}
                  onClick={() => onViewScopeChange(viewScope === 'full' ? 'participant' : 'full')}
                  className="h-6 w-6 shrink-0 p-0 text-muted-foreground hover:text-foreground"
                >
                  {switchingViewScope ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : viewScope === 'full' ? (
                    <EyeOff className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <Eye className="h-3.5 w-3.5" aria-hidden />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{scopeLabel}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          aria-label="退出会话"
          disabled={leaving}
          onClick={handleLeave}
          className="h-6 shrink-0 gap-1 rounded-lg px-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          {leaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <LogOut className="h-3.5 w-3.5" aria-hidden />}
          退出会话
        </Button>
      </div>
    </div>
  );
}