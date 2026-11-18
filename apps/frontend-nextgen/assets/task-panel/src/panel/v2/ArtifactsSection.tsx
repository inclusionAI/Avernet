import React, { useState } from 'react';
import { downloadTaskArtifact, type TaskArtifactAccessContext } from '../hooks/useTaskArtifactPreview';
import { useTaskPanelHost } from '../hostContext';
import { Empty as StateEmpty } from '../theme';
import { C, PANEL_CSS_VARIABLES } from '../tokens';
import type { TaskArtifactView } from '../types';
import { ArtifactListItem } from './ArtifactListItem';
import { TaskArtifactPreviewDrawer } from './TaskArtifactPreviewDrawer';

export interface ArtifactsSectionProps {
  artifacts: TaskArtifactView[];
  apiBaseUrl: string;
  context: TaskArtifactAccessContext;
  title?: string;
  previewPortalContainer?: HTMLElement | null;
}

/** Shared file list for the task side panel and the Lab's “view artifacts” drawer. */
export const ArtifactsSection: React.FC<ArtifactsSectionProps> = ({
  artifacts,
  context,
  title = '产物',
  previewPortalContainer,
}) => {
  const host = useTaskPanelHost();
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const selectedArtifact = artifacts.find((artifact) => artifact.id === selectedArtifactId) ?? null;
  const download = async (artifact: TaskArtifactView) => {
    setDownloadingId(artifact.id);
    setDownloadError(null);
    try {
      await downloadTaskArtifact(artifact, { ...context, host });
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : '产物下载失败，请稍后重试。');
    } finally {
      setDownloadingId(null);
    }
  };
  return (
    <>
      <section data-testid="artifacts-section" style={{ ...PANEL_CSS_VARIABLES, borderTop: `1px solid ${C.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px 0' }}>
          <span style={{ fontSize: 14, fontWeight: 500, color: C.textPrimary }}>{title}</span>
          <span title="产物计数" style={{ fontSize: 12, color: C.textSecondary }}>
            {artifacts.length} 项
          </span>
        </div>
        {downloadError && (
          <div role="alert" style={{ padding: '8px 20px 0', color: C.danger, fontSize: 12 }}>
            {downloadError}
          </div>
        )}
        {artifacts.length === 0 ? (
          <StateEmpty description="暂无产物" minHeight={120} />
        ) : (
          <ul
            style={{
              listStyle: 'none',
              margin: 0,
              padding: '12px 20px 20px',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}
          >
            {artifacts.map((artifact) => (
              <ArtifactListItem
                key={artifact.id}
                artifact={artifact}
                downloading={downloadingId === artifact.id}
                onPreview={() => setSelectedArtifactId(artifact.id)}
                onDownload={() => void download(artifact)}
              />
            ))}
          </ul>
        )}
      </section>
      {selectedArtifact && (
        <TaskArtifactPreviewDrawer
          artifact={selectedArtifact}
          context={context}
          onClose={() => setSelectedArtifactId(null)}
          portalContainer={previewPortalContainer}
        />
      )}
    </>
  );
};
export default ArtifactsSection;
