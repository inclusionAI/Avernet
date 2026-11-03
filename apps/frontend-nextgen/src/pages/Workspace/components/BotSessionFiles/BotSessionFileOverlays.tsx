import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import type { UseBotSessionFileUploadResult } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import type { UseBotSessionFilesResult } from '@/pages/Workspace/hooks/useBotSessionFiles';
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { WorkspaceOverlayPanel } from '../WorkspaceOverlayPanel';
import { BotSessionFilesPanel } from './BotSessionFilesPanel';
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
}

/** 文件覆盖层只负责展示；运行阶段和所有业务回调由会话 Hook 传入。 */
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
      {props.drawerOpen && (
        <WorkspaceOverlayPanel
          ariaLabel="资源管理面板"
          onDismiss={props.onCloseDrawer}
          {...WORKSPACE_RIGHT_PANEL_LAYOUT}
          className="z-30 bg-background"
        >
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
          />
        </WorkspaceOverlayPanel>
      )}
    </>
  );
}
