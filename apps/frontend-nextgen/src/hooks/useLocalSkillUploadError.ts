import { BackendRequestError } from '@/services/botWorkshop';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useState } from 'react';
import { isLocalSkillPackageError, localSkillUploadErrorMessage } from './localSkillUploadErrors';

export function useLocalSkillUploadError() {
  const [message, setMessage] = useState('');
  const handleError = (error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    if (error instanceof BackendRequestError) {
      if (isLocalSkillPackageError(error.data) || error.status === 413) {
        if (error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
        setMessage(localSkillUploadErrorMessage(error.data, error.status));
      } else if (error.status === 404 || error.status === 409 || error.status === 422 || (error.status !== undefined && error.status >= 500)) {
        if (error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
        if (error.status === 404) setMessage('当前 Bot 不存在或无访问权限，请刷新页面后重试。');
        else if (error.status === 409) setMessage('当前无法上传 Skill，请稍后重试。');
        else if (error.status === 422) setMessage('上传文件信息不正确，请重新选择后重试。');
        else setMessage('Skill 服务暂时不可用，上传结果可能未确认，请刷新查看后重试。');
      }
    } else if (!(error instanceof Error && error.name === 'AceLoginRedirectError')) {
      setMessage('Skill 上传失败，请检查网络或目录权限后重试。');
    }
  };
  return { message, setMessage, handleError };
}
