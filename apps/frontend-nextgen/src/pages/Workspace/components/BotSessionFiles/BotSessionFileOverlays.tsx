import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import type { UseBotSessionFileUploadResult } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import type { UseBotSessionFilesResult } from '@/pages/Workspace/hooks/useBotSessionFiles';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { ResizableWorkspaceSidebar } from '../ResizableWorkspaceSidebar';
import { BotSessionFilesPanel } from './BotSessionFilesPanel';
import type { BotResourceJump } from './BotSessionFilesPanel';
import { BotUploadFilesModal } from './UploadFilesModal';

interface BotSessionFileOverlaysProps {
  botId: string | null;
  sessionId: string | null;
  userId: string | null;
  ownerId?: string;
  stage?: BotRuntimeStage;
  files: UseBotSessionFilesResult;
  upload: UseBotSessionFileUploadResult;
  drawerOpen: boolean;
  uploadOpen: boolean;
  onCloseDrawer(): void;
  onCloseUpload(): void;
  onOpenUpload(): void;
  onAddToSession(): void;
  onReference(file: BotSessionFileView): void;
  /** 跳转 Bot 完整资源目录入口；不传则不渲染（好友 Bot 等场景）。 */
  resourceJump?: BotResourceJump;
}

/**
 * 文件副屏容器 + 资源管理面板：覆盖层只负责展示；运行阶段和所有业务回调由会话 Hook 传入。
 * aside 常挂、drawerOpen 开合（对话区内联展开/收起，与头部右缘副屏及 SDK 副屏同构，无抽屉蒙层）；
 * BotSessionFilesPanel 保持「挂载 == 打开」的列表刷新语义，仅展开态渲染。
 */
export function BotSessionFileOverlays(props: BotSessionFileOverlaysProps) {
  const { botId, sessionId, userId, ownerId, stage, files, upload } = props;
  return (
    <>
      <BotUploadFilesModal
        open={props.uploadOpen}
        onClose={props.onCloseUpload}
        queue={upload.tasks}
        isUploading={upload.isUploading}
        stageFiles={upload.stageFiles}
        submit={async () => {
          await upload.submit();
        }}
        onAddToSession={props.onAddToSession}
        removeTask={upload.removeTask}
      />
      <ResizableWorkspaceSidebar
        ariaLabel="资源管理面板"
        side="right"
        open={props.drawerOpen}
        onDismiss={props.drawerOpen ? props.onCloseDrawer : undefined}
        {...WORKSPACE_RIGHT_PANEL_LAYOUT}
        className="bg-background"
      >
        {props.drawerOpen ? (
          <BotSessionFilesPanel
            readyFiles={files.readyFiles}
            isLoadingList={files.isLoadingList}
            botId={botId ?? undefined}
            sessionId={sessionId ?? undefined}
            userId={userId ?? undefined}
            ownerId={ownerId}
            stage={stage}
            onClose={props.onCloseDrawer}
            onUploadClick={props.onOpenUpload}
            onOpen={() => void files.refresh()}
            onDelete={(file) => void files.deleteFile(file)}
            onDownload={(file) => void files.downloadFile(file)}
            onReference={props.onReference}
            resourceJump={props.resourceJump}
          />
        ) : null}
      </ResizableWorkspaceSidebar>
    </>
  );
}
