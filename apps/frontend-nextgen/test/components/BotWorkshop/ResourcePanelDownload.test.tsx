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
