// src/components/BotWorkshop/Editor/useLocalSkillFolderUpload.ts
//
// 「我的 Skill」文件夹上传逻辑 hook（CapabilityPickerModal 专用）：目录选择、
// 文件读取、上传中状态与错误提示收口；上传成功后把新 Skill id 并入勾选。

import { useLocalSkillUploadError } from '@/hooks/useLocalSkillUploadError';
import type { BotEditorSkill } from '@/domain/botEditor';
import { useState } from 'react';
import { readDirectoryFiles, type DirectoryHandle } from './capabilityPickerDirectory';

export function useLocalSkillFolderUpload(
  onUploadFolder: ((files: File[]) => Promise<BotEditorSkill>) | undefined,
  setSelected: React.Dispatch<React.SetStateAction<string[]>>,
) {
  const [uploading, setUploading] = useState(false);
  const uploadError = useLocalSkillUploadError();
  const canPickDirectory = typeof window !== 'undefined' && 'showDirectoryPicker' in window;
  const uploadFolder = async () => {
    if (!onUploadFolder || !canPickDirectory) return;
    setUploading(true);
    uploadError.setMessage('');
    try {
      const pickerWindow = window as Window & {
        showDirectoryPicker?: (options?: { mode?: 'read' }) => Promise<DirectoryHandle>;
      };
      const directoryHandle = await pickerWindow.showDirectoryPicker?.({ mode: 'read' });
      if (!directoryHandle) return;
      const files = await readDirectoryFiles(directoryHandle);
      if (files.length) {
        const skill = await onUploadFolder(files);
        setSelected((current) => [...new Set([...current, skill.id])]);
      } else {
        uploadError.setMessage('所选目录没有可上传的文件，请确认目录包含 SKILL.md。');
      }
    } catch (error) {
      uploadError.handleError(error);
    } finally {
      setUploading(false);
    }
  };
  return { uploading, uploadError, canPickDirectory, uploadFolder };
}
