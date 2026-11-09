// 单聊与协作群共用的右缘面板容器，不依赖单聊页面树。
import { IconButton } from '@/components/ui';
import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import { ResizableWorkspaceSidebar } from './ResizableWorkspaceSidebar';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

export interface ChatHeaderRightPanelProps {
  title: string;
  onClose: () => void;
  /** 展开态；false → 宽度收 0 + 内容退出可交互，容器保持挂载形成展开/收起过渡。 */
  open?: boolean;
  children: ReactNode;
}

/** 面板容器：48px 头行（14px 标题 + 关闭钮）+ 内容列。 */
export function ChatHeaderRightPanel({ title, onClose, open = true, children }: ChatHeaderRightPanelProps) {
  return (
    <ResizableWorkspaceSidebar
      ariaLabel={`${title}面板`}
      side="right"
      open={open}
      onDismiss={open ? onClose : undefined}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="bg-card"
    >
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-divider px-4">
          <h3 className="m-0 text-sm font-medium text-foreground">{title}</h3>
          <IconButton label="关闭面板" icon={<X className="size-4" />} variant="ghost" size="sm" onClick={onClose} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">{children}</div>
      </div>
    </ResizableWorkspaceSidebar>
  );
}

