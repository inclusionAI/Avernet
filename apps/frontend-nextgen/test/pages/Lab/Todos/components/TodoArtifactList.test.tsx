/** @jest-environment jsdom */
import type { TodoArtifact } from '@/domain/todos/models';
import { TodoArtifactList } from '@/pages/Lab/Todos/components/TodoArtifactList';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const onReadFile = jest.fn<Promise<Blob>, [string, 'inline' | 'attachment']>();

function fileArtifact(fileName: string, fileType: string): Extract<TodoArtifact, { kind: 'file' }> {
  return {
    artifactId: `artifact-${fileName}`,
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: fileName,
    kind: 'file',
    content: { fileName, fileType, size: 2048, isFinalArtifact: true },
    createdAt: '2026-11-16T01:00:00Z',
  };
}

beforeEach(() => {
  onReadFile.mockReset();
});

test('不支持浏览器预览的产物文件禁用预览且保留下载', async () => {
  render(
    <TodoArtifactList
      artifacts={[fileArtifact('定价与功能矩阵.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]}
      onReadFile={onReadFile}
    />,
  );

  const preview = screen.getByRole('button', { name: '预览' });
  expect(preview).toBeDisabled();
  expect(screen.getByText('该格式暂不支持在线预览')).toBeInTheDocument();
  expect(screen.getByText('该格式暂不支持在线预览')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '下载' })).toBeEnabled();

  fireEvent.click(preview);
  expect(onReadFile).not.toHaveBeenCalled();
});

test('Markdown 文件产物在页面内渲染预览，不再另开下载', async () => {
  const createObjectURL = jest.fn(() => 'blob:todo-artifact-preview');
  const revokeObjectURL = jest.fn();
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
  const anchorClick = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  onReadFile.mockResolvedValue(
    new Blob(['# 定价与功能矩阵\n\n- 产品 A：免费\n'], { type: 'text/markdown' }),
  );

  render(
    <TodoArtifactList
      artifacts={[fileArtifact('定价与功能矩阵.md', 'application/octet-stream')]}
      onReadFile={onReadFile}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '预览' }));

  await waitFor(() => expect(onReadFile).toHaveBeenCalledWith('artifact-定价与功能矩阵.md', 'inline'));
  expect(await screen.findByRole('heading', { name: '定价与功能矩阵' })).toBeInTheDocument();
  expect(screen.getByText('文本')).toBeInTheDocument();
  expect(screen.getByText('产品 A：免费')).toBeInTheDocument();
  expect(screen.queryByText('该格式暂不支持在线预览')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /关闭文件预览/ }));
  anchorClick.mockRestore();
  expect(anchorClick).not.toHaveBeenCalled();
});
