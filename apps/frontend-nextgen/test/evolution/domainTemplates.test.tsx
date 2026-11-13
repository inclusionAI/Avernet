/** @jest-environment jsdom */
import type { Template } from '@/domain/evolution/types';
import { DomainTemplates } from '@/pages/EvolutionFactory/DomainTemplates';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

jest.mock('@umijs/max', () => ({ history: { push: require('@jest/globals').jest.fn() } }));

const templates: Template[] = Array.from({ length: 13 }, (_, i) => ({
  templateName: `template-${i + 1}`,
  displayName: `评测 ${i + 1}`,
  description: null,
  status: i === 0 ? 'published' : 'draft',
  latestVersion: 2,
  publishedVersion: i === 0 ? 1 : null,
  versions: [{ version: 2, status: i === 0 ? 'published' : 'draft' }],
}));
const startTemplate = jest.fn<(_: string) => Promise<void>>().mockResolvedValue(undefined);
const archiveTemplate = jest.fn<(_: string) => Promise<void>>().mockResolvedValue(undefined);
function Harness({ data = templates, writable = true }: { data?: Template[]; writable?: boolean }) {
  const [selected, setSelected] = useState<string[]>([]);
  return (
    <DomainTemplates
      templates={{ data, loading: false, error: '', reload: () => {} }}
      writable={writable}
      selected={selected}
      setSelected={setSelected}
      busy={false}
      startTemplate={startTemplate}
      archiveTemplate={archiveTemplate}
    />
  );
}

it('paginates real templates and preserves draft selections across pages', () => {
  render(<Harness />);
  expect(screen.getAllByRole('row')).toHaveLength(11);
  expect(screen.getByText('共 13 条')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择模板 template-1' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: '选择模板 template-2' }));
  fireEvent.click(screen.getByRole('button', { name: '第 2 页' }));
  expect(screen.getAllByRole('row')).toHaveLength(4);
  expect(screen.queryByRole('checkbox', { name: '选择模板 template-2' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: '选择模板 template-11' }));
  expect(screen.getByText('已选择 2 个模板')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '第 1 页' }));
  expect(screen.getByRole('checkbox', { name: '选择模板 template-2' })).toBeChecked();
  fireEvent.click(screen.getAllByRole('button', { name: '编辑' })[1]);
  expect(startTemplate).toHaveBeenCalledWith('template-2');
});

it('clamps a removed last page and keeps read-only permissions intact', () => {
  const view = render(<Harness writable={false} />);
  expect(screen.queryByRole('button', { name: '归档' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择模板 template-2' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '第 2 页' }));
  view.rerender(<Harness writable={false} data={templates.slice(0, 3)} />);
  expect(screen.getByRole('button', { name: '第 1 页' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getAllByRole('row')).toHaveLength(4);
  view.rerender(<Harness writable={false} data={[]} />);
  expect(screen.getByText('暂无模板')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '第 1 页' })).not.toBeInTheDocument();
});
