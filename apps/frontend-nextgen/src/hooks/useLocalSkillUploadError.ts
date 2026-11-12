import { BackendRequestError } from '@/services/botWorkshop';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useState } from 'react';
import { isLocalSkillPackageError, localSkillUploadErrorMessage } from './localSkillUploadErrors';

export function useLocalSkillUploadError() {
  const [message, setMessage] = useState('');
  const handleError = (error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    if (error instanceof BackendRequestError) {
      if (isLocalSkillPackageError(error.data)) {
        if (error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
        setMessage(localSkillUploadErrorMessage(error.data));
      }
    } else if (!(error instanceof Error && error.name === 'AceLoginRedirectError')) {
      setMessage('Skill 上传失败，请检查网络或目录权限后重试。');
    }
  };
  return { message, setMessage, handleError };
}
