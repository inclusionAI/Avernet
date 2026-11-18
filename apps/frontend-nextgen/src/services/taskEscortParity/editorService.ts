import {
  editorController,
  type EditorController,
  type EditorIdentityParams,
} from '@/services/backendApi/harnessFlow/editorController';
import {
  editorPayload,
  mapEditorAccess,
  mapEditorDiff,
  mapEditorHistory,
  mapEditorPack,
  mapEditorSnapshot,
  mapEditorSpec,
} from './editorMapper';

/** No global store or UI side effects; every read receives its bound identity explicitly. */
export function createEditorService(api: EditorController = editorController) {
  return {
    async workflow(id: string, params: EditorIdentityParams, signal?: AbortSignal) {
      return mapEditorSpec(editorPayload(await api.workflow(id, params, signal)), id);
    },
    async access(id: string, params: EditorIdentityParams, signal?: AbortSignal) {
      return mapEditorAccess(editorPayload(await api.access(id, params, signal)), id);
    },
    async history(id: string, params: EditorIdentityParams, signal?: AbortSignal) {
      return mapEditorHistory(editorPayload(await api.history(id, params, signal)), id);
    },
    async snapshot(id: string, deploy: number, params: EditorIdentityParams, signal?: AbortSignal) {
      if (!Number.isSafeInteger(deploy) || deploy < 0) throw new Error('发布序号无效');
      return mapEditorSnapshot(editorPayload(await api.snapshot(id, deploy, params, signal)), id, deploy);
    },
    async diff(id: string, from: number, to: number, params: EditorIdentityParams, signal?: AbortSignal) {
      if (![from, to].every((number) => Number.isSafeInteger(number) && number >= 0))
        throw new Error('对比发布序号无效');
      return mapEditorDiff(editorPayload(await api.diff(id, from, to, params, signal)), id, from, to);
    },
    async scripts(id: string, params: EditorIdentityParams, signal?: AbortSignal) {
      try {
        return mapEditorPack(editorPayload(await api.scripts(id, params, signal)), id);
      } catch (error) {
        const status = error && typeof error === 'object' && 'status' in error ? error.status : null;
        if (status === 403) throw new Error('没有工作流编辑权限或 Pack 读取权限，无法查看脚本。');
        if (status === 409) throw new Error('Pack 快照存在分支冲突，暂无法安全读取。');
        if (status === 404) throw new Error('未找到工作流或 Pack Git 快照。');
        if (status === 503) throw new Error('Pack 脚本服务暂不可用，请稍后重试。');
        throw error;
      }
    },
  };
}
export const editorService = createEditorService();
