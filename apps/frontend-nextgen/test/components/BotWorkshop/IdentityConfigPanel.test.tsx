/** @jest-environment jsdom */

import { IdentityConfigPanel } from '@/components/BotWorkshop/Editor/IdentityConfigPanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

const files = [
  { type: 'SOUL', exists: true },
  { type: 'AGENT', exists: true },
];

beforeEach(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

test('文档默认预览态,右上角工具栏含预览/刷新/编辑/保存四个按钮', async () => {
  render(
    <IdentityConfigPanel files={files} editable getFile={async (type) => `# ${type}`} onSave={jest.fn()} />,
  );

  for (const label of ['预览', '刷新', '编辑', '保存']) {
    expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
  }
  expect(await screen.findByText('# RULES')).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

test('编辑进入编辑态,修改后保存携带最新内容', async () => {
  const onSave = jest.fn().mockResolvedValue(undefined);
  render(
    <IdentityConfigPanel files={files} editable getFile={async (type) => `原文 ${type}`} onSave={onSave} />,
  );
  await screen.findByText('原文 RULES');

  fireEvent.click(screen.getByRole('button', { name: '编辑' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '改后的内容' } });
  fireEvent.click(screen.getByRole('button', { name: '保存' }));

  await waitFor(() => expect(onSave).toHaveBeenCalledWith('RULES', '改后的内容'));
});

test('刷新丢弃本地编辑并重新拉取当前文档', async () => {
  let remote = '第一版内容';
  const getFile = jest.fn(async () => remote);
  render(<IdentityConfigPanel files={files} editable getFile={getFile} onSave={jest.fn()} />);
  await screen.findByText('第一版内容');

  fireEvent.click(screen.getByRole('button', { name: '编辑' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '本地改动' } });
  remote = '第二版内容';
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  fireEvent.click(screen.getByRole('button', { name: '预览' }));

  expect(await screen.findByText('第二版内容')).toBeInTheDocument();
  expect(getFile).toHaveBeenCalledTimes(2);
});

test('只读访客停留在预览态且编辑/保存禁用', async () => {
  render(
    <IdentityConfigPanel files={files} editable={false} getFile={async () => '只读内容'} onSave={jest.fn()} />,
  );

  await screen.findByText('只读内容');
  expect(screen.getByRole('button', { name: '编辑' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

test('选中的文档以灰底呈现,不再使用品牌蓝(artboard-049)', async () => {
  render(
    <IdentityConfigPanel
      files={[
        { type: 'RULES', exists: false },
        { type: 'SOUL', exists: false },
      ]}
      editable
      getFile={async () => '# RULES'}
      onSave={jest.fn()}
    />,
  );

  // 标题 {type}.MD 也会渲染同名文本,这里只取文档列表按钮里的那个
  const listButton = (label: string) =>
    screen
      .getAllByText(label)
      .map((element) => element.closest('button'))
      .find(Boolean) as HTMLElement;

  const selected = listButton('RULES.MD');
  expect(selected?.className).toContain('bg-foreground/10');
  expect(selected?.className).not.toContain('bg-primary');
  expect(selected?.className).not.toContain('text-primary');

  const unselected = listButton('SOUL.MD');
  expect(unselected?.className).not.toContain('bg-foreground/10');
});

test('预览内容卡带 #D8D8D8 描边与白底(artboard-049)', async () => {
  render(<IdentityConfigPanel files={files} editable getFile={async () => '# RULES'} onSave={jest.fn()} />);

  await screen.findByText('# RULES');
  const previewCard = document.querySelector('.app-scrollbar');
  expect(previewCard?.className).toContain('border-[#D8D8D8]');
  expect(previewCard?.className).toContain('bg-background');
  expect(previewCard?.className).toContain('rounded-lg');
});

test('文件列表与右侧预览/操作区均无卡片外框,以分割线相隔并连接 header 底边', async () => {
  const { container } = render(
    <IdentityConfigPanel files={files} editable getFile={async () => '# RULES'} onSave={jest.fn()} />,
  );

  const aside = container.querySelector('aside');
  expect(aside?.className).not.toContain('border');
  expect(aside?.className).not.toContain('bg-card');
  // 文件列表宽度对齐设计稿 128px;不再渲染「文档列表」标题,顶边留白收敛为 pt-4 与右侧工具栏齐平
  expect(aside?.className).toContain('w-32');
  expect(aside?.className).not.toContain('py-6');
  expect(aside?.className).toContain('pt-4');
  expect(screen.queryByText('文档列表')).not.toBeInTheDocument();

  // 工具栏与预览卡之间不再有分割线
  const toolbarRow = container.querySelector('h3')?.parentElement;
  expect(toolbarRow?.className).not.toContain('border-b');

  // wrapper 顶边零留白,分割线才能贴着 title 底边往下走;左右留白收敛(不再 gap-5/px-6/lg:px-10)
  expect(aside?.parentElement?.className).not.toContain('py-6');
  expect(aside?.parentElement?.className).toContain('px-4');
  expect(aside?.parentElement?.className).toContain('lg:px-6');
  expect(aside?.parentElement?.className).not.toContain('gap-5');

  // 分割线与 header 的 border-b 同色(border)同粗(1px)
  const divider = container.querySelector('.w-px');
  expect(divider?.className).toContain('bg-divider');
  expect(divider?.className).not.toContain('bg-divider/55');

  // 右侧预览/操作区不再有外框卡片;分割线右侧 20px 走道对齐设计稿
  const pane = container.querySelector('h3')?.parentElement?.parentElement;
  expect(pane?.className).not.toContain('border');
  expect(pane?.className).not.toContain('rounded-xl');
  expect(pane?.className).not.toContain('shadow-sm');
  expect(pane?.className).not.toContain('bg-card');
  expect(pane?.className).toContain('md:ml-5');
});
