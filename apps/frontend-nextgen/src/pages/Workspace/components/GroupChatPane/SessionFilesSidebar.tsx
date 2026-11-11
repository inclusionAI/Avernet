import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import { WorkspaceOverlayPanel } from '../WorkspaceOverlayPanel';
import { SessionFilesPanel, type SessionFilesPanelProps } from './SessionFilesPanel';

export type SessionFilesSidebarProps = SessionFilesPanelProps;

/**
 * 资源管理覆盖面板（群聊侧接线用）：WorkspaceOverlayPanel + SessionFilesPanel
 * 的固定组合，与单聊侧 useBotSessionFilesFeature 的容器参数同构
 * （320-600px 可拖宽，默认 380，宽度持久化）。
 */
export function SessionFilesSidebar(props: SessionFilesSidebarProps) {
  return (
    <WorkspaceOverlayPanel
      ariaLabel="资源管理面板"
      onDismiss={props.onClose}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="z-30 bg-background"
    >
      <SessionFilesPanel {...props} />
    </WorkspaceOverlayPanel>
  );
}

export default SessionFilesSidebar;
