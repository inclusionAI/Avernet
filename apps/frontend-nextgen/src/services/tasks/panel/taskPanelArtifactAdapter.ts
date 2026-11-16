import { getPreviewKind } from '@/services/workspace/sessionFileUtils';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { downloadTaskArtifactContent } from '@/services/backendApi/tasks/taskController';
import type { TaskArtifactReader } from '@avernet-assets/task-panel';
export const taskPanelArtifacts: TaskArtifactReader = {
  contentUrl(artifact) {
    const ref = artifact.fileRef;
    return ref?.storageType === 'session_file' && ref.fileId ? sessionFileService.buildContentUrl(ref.storageId, ref.fileId) : null;
  },
  async readContent(artifact, owner, signal) {
    const ref = artifact.fileRef;
    if (!ref) throw new Error('产物文件引用不完整。');
    let blob: Blob;
    if (ref.storageType === 'object_file') blob = await downloadTaskArtifactContent(artifact.id);
    else if (ref.storageType === 'session_file') {
      if (!ref.fileId) throw new Error('会话文件引用不完整。');
      const result = await sessionFileService.fetchContentBlob(ref.storageId, ref.fileId);
      if (!result.ok) throw Object.assign(new Error(result.error.friendlyMessage), { friendlyMessage: result.error.friendlyMessage });
      blob = result.data;
    } else if (getPreviewKind(artifact.name, ref.fileType ?? undefined) === 'text') {
      const preview = await botEditorService.previewResource(owner.ownerBotId, ref.storageId, owner.ownerUserId);
      if (preview.kind !== 'text') throw new Error('预览加载失败，请稍后重试。');
      blob = new Blob([preview.content], { type: preview.contentType });
    } else blob = await botEditorService.downloadResource(owner.ownerBotId, ref.storageId, owner.ownerUserId);
    signal.throwIfAborted(); return blob;
  },
  async download(artifact, owner, signal) {
    const ref = artifact.fileRef;
    if (ref?.storageType === 'session_file') {
      if (!ref.fileId) throw new Error('会话文件引用不完整，无法下载。');
      signal.throwIfAborted(); const anchor = document.createElement('a');
      anchor.href = sessionFileService.buildDownloadUrl(ref.storageId, ref.fileId); anchor.download = artifact.name;
      anchor.target = '_blank'; anchor.rel = 'noopener'; document.body.appendChild(anchor); anchor.click(); anchor.remove(); return;
    }
    const blob = ref && ref.storageType !== 'object_file'
      ? await botEditorService.downloadResource(owner.ownerBotId, ref.storageId, owner.ownerUserId)
      : await taskPanelArtifacts.readContent(artifact, owner, signal);
    signal.throwIfAborted(); const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = artifact.name;
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
  },
};
