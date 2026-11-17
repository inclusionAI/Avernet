/** @jest-environment jsdom */

import { ResourcePanel } from '@/components/BotWorkshop/Editor/ResourcePanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

test('文件和文件夹均展示下载入口，并传递正确资源类型', () => {
  const onDownload = jest.fn().mockResolvedValue(undefined);
  render(
    <ResourcePanel
      resources={[
        { path: 'docs', parentPath: '', name: 'docs', type: 'folder' },
        { path: 'README.md', parentPath: '', name: 'README.md', type: 'file' },
      ]}
      editable
      onCreateDirectory={jest.fn()}
      onDelete={jest.fn()}
      onUpload={jest.fn()}
      onPreview={jest.fn().mockResolvedValue({ kind: 'text', content: '', contentType: 'text/plain' })}
      onCopyPath={jest.fn().mockResolvedValue(undefined)}
      onDownload={onDownload}
      onLoadDirectory={jest.fn()}
      loadingPaths={[]}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '下载文件夹docs' }));
  fireEvent.click(screen.getByRole('button', { name: '下载文件README.md' }));

  expect(onDownload).toHaveBeenNthCalledWith(1, 'docs', 'folder');
  expect(onDownload).toHaveBeenNthCalledWith(2, 'README.md', 'file');
});


test('加载失败展示提示和重试，不显示空目录', () => {
  const retry = jest.fn().mockResolvedValue(undefined);
  render(<ResourcePanel resources={[]} editable={false} onCreateDirectory={jest.fn()} onDelete={jest.fn()}
    onUpload={jest.fn()} onPreview={jest.fn()} onCopyPath={jest.fn()} onDownload={jest.fn()}
    onLoadDirectory={jest.fn()} loadingPaths={[]} errors={{ '': 'Bot 暂时离线，请稍后重试' }} onRetry={retry} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Bot 暂时离线');
  expect(screen.queryByText('工作区为空')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(retry).toHaveBeenCalledWith('');
});
