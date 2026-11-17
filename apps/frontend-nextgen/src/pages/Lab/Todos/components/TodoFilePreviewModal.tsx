import { Modal, ModalContent, ModalTitle } from '@/components/ui/Modal';
import { PreviewPane } from '@/pages/Workspace/components/GroupChatPane/SessionFilesPreviewPane';
import { useTodoFilePreview, type TodoFileArtifact } from './useTodoFilePreview';

interface TodoFilePreviewModalProps {
  artifact: TodoFileArtifact | null;
  onOpenChange: (artifact: TodoFileArtifact | null) => void;
  onDownload: (artifact: TodoFileArtifact) => void;
  readArtifact: (artifactId: string, disposition: 'inline' | 'attachment') => Promise<Blob>;
}

export function TodoFilePreviewModal({
  artifact,
  onOpenChange,
  onDownload,
  readArtifact,
}: TodoFilePreviewModalProps) {
  const internalPreview = useTodoFilePreview(artifact, readArtifact);

  return (
    <Modal
      open={artifact !== null}
      onOpenChange={(open) => {
        if (!open) onOpenChange(null);
      }}
    >
      <ModalContent
        size="full"
        className="h-[calc(100vh-4rem)] grid-rows-[auto_minmax(0,1fr)] p-4"
        closeLabel="关闭文件预览"
        aria-describedby={undefined}
      >
        <ModalTitle className="sr-only">待办产物文件预览</ModalTitle>
        <PreviewPane
          file={
            artifact
              ? { name: artifact.content.fileName || artifact.artifactName, size: artifact.content.size }
              : null
          }
          filesStateEmpty={false}
          preview={internalPreview}
          onDownload={() => {
            if (artifact) onDownload(artifact);
          }}
        />
      </ModalContent>
    </Modal>
  );
}
