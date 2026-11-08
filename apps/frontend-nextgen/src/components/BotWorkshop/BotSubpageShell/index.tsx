import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { Button } from '@/components/ui/Button';
import { ArrowLeft } from 'lucide-react';
import React from 'react';

export interface BotSubpageShellProps {
  /** 返回动作（通常是 history.push('/bot-workshop')）。 */
  onBack: () => void;
  /** 用于顶栏头像与无障碍标注的 Bot 名称。 */
  botName: string;
  /** 顶栏主标题（如「日志 · PPT助手」）。 */
  title: string;
  /** 顶栏副标题；空字符串 / undefined 时整体不渲染。 */
  subtitle?: string;
  /** 返回按钮的无障碍描述，缺省「返回 Bot 管理」。 */
  backAriaLabel?: string;
  /** 顶栏右侧操作区（状态徽标、刷新等）。 */
  actions?: React.ReactNode;
  /** 内容区业务内容；灰画布与留白由壳承担，白卡由接入方按需排布。 */
  children: React.ReactNode;
}

/**
 * Bot 管理二级页统一壳：顶栏（返回 + Bot 头像 + 标题/副标题 + actions）
 * + 灰画布滚动内容区，与首页「灰画布 + 白卡」基线同族。
 * 只服务 Bot 管理列表族子页（logs / health-check / task-instances），
 * 不承载 Bot 编辑器（/bot-workshop/detail 有独立 bot-editor-shell）。
 */
export function BotSubpageShell({
  onBack,
  botName,
  title,
  subtitle,
  backAriaLabel = '返回 Bot 管理',
  actions,
  children,
}: BotSubpageShellProps) {
  return (
    <main className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          aria-label={backAriaLabel}
          onClick={onBack}
          leftIcon={<ArrowLeft className="size-4" />}
        />
        <BotAvatar name={botName} className="h-9 w-9 rounded-lg" />
        <div className="min-w-0 flex-1">
          <h1 className="m-0 truncate text-base font-semibold">{title}</h1>
          {subtitle ? <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </header>
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-page-canvas">
        <div className="w-full p-4 sm:p-6 2xl:p-8">{children}</div>
      </div>
    </main>
  );
}
