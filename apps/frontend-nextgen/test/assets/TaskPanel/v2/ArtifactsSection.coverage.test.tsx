/** @jest-environment jsdom */
// 产物下载异常分支:下载失败必须把错误透传到卡片错误条(Error→message,非 Error→兜底文案),
// 下载中按钮禁用、结束后恢复 —— ArtifactsSection.tsx download() catch/finally 路径。
import { ArtifactsSection } from '@/assets/TaskPanel/v2/ArtifactsSection';
import { downloadTaskArtifact } from '@/assets/TaskPanel/hooks/useTaskArtifactPreview';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
// 注意:jest.mock factory 会被 hoist,内部的 jest 必须是运行时注入的全局对象,
// 不能从 @jest/globals import(局部绑定在 factory 执行时尚未初始化,参照
// test/components/Community/PersonalCenter.coverage.test.tsx 的既有模式)。
import { beforeEach, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// jest.requireActual 在 @jest-globals 注入模式下不可用;ArtifactsSection 只消费
// downloadTaskArtifact 一个值导出,直接整体替换即可。
jest.mock('@/assets/TaskPanel/hooks/useTaskArtifactPreview', () => ({
  downloadTaskArtifact: jest.fn(),
}));

const fileArtifact: TaskArtifactView = {
  id: 'artifact-dl',
  name: '可下载产物',
  type: 'file',
  kind: 'file',
  summary: null,
  owner: '张三',
  updatedAt: '2026-10-23T10:00:00.000Z',
  // fileRef 只需非空即可渲染下载入口(ArtifactsSection 仅判 fileRef == null);fileName 不是
  // TaskArtifactView.fileRef 的字段,去除并补上协议必需的 storageType(session_file + fileId 成对)。
  fileRef: { storageType: 'session_file', storageId: 'storage-1', fileId: 'file-1' },
  nodeId: null,
};

const context = { taskId: 'task-1', ownerBotId: 'bot-1', ownerUserId: 'user-1' };

describe('ArtifactsSection 下载异常分支', () => {
  beforeEach(() => {
    (downloadTaskArtifact as jest.Mock).mockReset();
  });

  it('下载 reject 非 Error 实例时展示兜底文案并复位按钮', async () => {
    (downloadTaskArtifact as jest.Mock).mockRejectedValue('boom' as never);
    render(<ArtifactsSection artifacts={[fileArtifact]} apiBaseUrl="" context={context} />);

    await fireEvent.click(screen.getByRole('button', { name: '下载产物 可下载产物' }));

    expect(downloadTaskArtifact).toHaveBeenCalledWith(fileArtifact, context);
    await screen.findByText('产物下载失败，请稍后重试。');
    expect(screen.getByRole('button', { name: '下载产物 可下载产物' })).not.toBeDisabled();
  });

  it('下载 reject Error 实例时透传 message', async () => {
    (downloadTaskArtifact as jest.Mock).mockRejectedValueOnce(new Error('对象存储 403'));
    render(<ArtifactsSection artifacts={[fileArtifact]} apiBaseUrl="" context={context} />);

    await fireEvent.click(screen.getByRole('button', { name: '下载产物 可下载产物' }));

    await screen.findByText('对象存储 403');
    // 错误条出现下一次下载前不清除旧错误(组件内仅 catch 写,无 next-success 清理路径)。
    expect(screen.queryByText('产物下载失败，请稍后重试。')).toBeNull();
  });
});
