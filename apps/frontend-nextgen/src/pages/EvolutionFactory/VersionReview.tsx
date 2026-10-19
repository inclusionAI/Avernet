import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Spin } from '@/components/ui/Spin';
import type { EvolutionVersion } from '@/domain/evolution/types';
import { ErrorBox } from './shared';
import { MarkdownReport } from './StepOutput';
import { versionLabel } from './VersionActions';

export function VersionReview({
  review,
  onClose,
  diff,
  error,
  loading,
}: {
  review?: EvolutionVersion;
  onClose: () => void;
  diff: string;
  error: string;
  loading: boolean;
}) {
  return (
    <Modal
      open={!!review}
      onOpenChange={(open) => {
        if (!open) {
          onClose();
        }
      }}
    >
      <ModalContent size="lg" className="max-w-[960px]" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>{review ? versionLabel(review) : ''} · 变更 Diff</ModalTitle>
        </ModalHeader>
        {review?.diff?.summary && <MarkdownReport content={review.diff.summary} />}
        {!!review?.diff?.files?.length && (
          <ul className="max-h-48 space-y-1 overflow-auto rounded-lg bg-muted p-3 font-mono text-xs">
            {review.diff.files.map((file, i) => (
              <li key={i} className="break-all">
                {typeof file === 'string' ? file : JSON.stringify(file)}
              </li>
            ))}
          </ul>
        )}
        <ErrorBox message={error} />
        {loading ? (
          <Spin tip="加载 Diff…" />
        ) : (
          <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-4 font-mono text-xs">
            {diff ||
              (review?.diff?.artifactAvailable
                ? '暂无 Diff 正文'
                : '该历史版本未登记完整 Diff Artifact；以上为上报摘要和文件列表。')}
          </pre>
        )}
      </ModalContent>
    </Modal>
  );
}
