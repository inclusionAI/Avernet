import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import { ResizableWorkspaceSidebar } from '../ResizableWorkspaceSidebar';
import { SessionFilesPanel, type SessionFilesPanelProps } from './SessionFilesPanel';

export type SessionFilesSidebarProps = SessionFilesPanelProps & {
  /** 展开态；false → 宽度收 0，容器保持挂载形成展开/收起过渡。 */
  open?: boolean;
};

/**
 * 资源管理内联副屏（群聊侧接线用）：ResizableWorkspaceSidebar + SessionFilesPanel
 * 的固定组合，与单聊侧 BotSessionFileOverlays 的容器同构（对话区内联展开/收起、
 * 无蒙层抽屉；320-600px 可拖宽，默认 380，宽度持久化）。
 * SessionFilesPanel 保持「挂载 == 打开」的列表刷新语义，仅展开态渲染。
 */
export function SessionFilesSidebar({ open = true, ...props }: SessionFilesSidebarProps) {
  return (
    <ResizableWorkspaceSidebar
      ariaLabel="资源管理面板"
      side="right"
      open={open}
      onDismiss={open ? props.onClose : undefined}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="bg-background"
    >
      {open ? <SessionFilesPanel {...props} /> : null}
    </ResizableWorkspaceSidebar>
  );
}

export default SessionFilesSidebar;
