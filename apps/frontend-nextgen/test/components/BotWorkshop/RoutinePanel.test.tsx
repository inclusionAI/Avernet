/** @jest-environment jsdom */

import { RoutinePanel } from '@/components/BotWorkshop/Editor/RoutinePanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const routine = {
  id: 'routine-1',
  name: '晨报',
  cron: '0 9 * * 1-5',
  command: '整理昨日进展',
  enabled: true,
  timezone: 'Asia/Shanghai',
  modifiedAt: '2026-08-18T11:20:00+08:00',
  nextRunAt: '2026-08-19T09:00:00+08:00',
};

const loadModels = jest.fn().mockResolvedValue([{ id: 'qwen-max', name: 'Qwen Max', provider: 'qwen' }]);
beforeEach(() => loadModels.mockClear());

test('编辑页仅保留配置、启停和删除，不展示立即执行与执行记录', () => {
  render(
    <RoutinePanel
      routines={[routine]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(screen.getByText('晨报')).toBeInTheDocument();
  expect(screen.getByRole('switch')).toBeInTheDocument();
  expect(screen.queryByText('立即执行')).not.toBeInTheDocument();
  expect(screen.queryByText('执行记录')).not.toBeInTheDocument();
});

test('任务卡片按摘要布局展示更新时间、频率、下次执行、执行配置和 Prompt', () => {
  render(
    <RoutinePanel
      routines={[routine]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(screen.getByText(/更新时间：/)).toBeInTheDocument();
  expect(screen.getByText('执行频率')).toBeInTheDocument();
  expect(screen.getByText('下次执行')).toBeInTheDocument();
  expect(screen.getByText('执行配置')).toBeInTheDocument();
  expect(screen.getByText('Prompt')).toBeInTheDocument();
  expect(screen.getByText('整理昨日进展')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '编辑晨报' })).toHaveTextContent('编辑');
  expect(screen.getByRole('button', { name: '删除晨报' })).toHaveTextContent('删除');
});

test('后端未返回下次执行时间时仍渲染该字段并以 — 占位', () => {
  const routineWithoutNextRun = { ...routine, nextRunAt: undefined };
  render(
    <RoutinePanel
      routines={[routineWithoutNextRun]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(screen.getByText('下次执行')).toBeInTheDocument();
  expect(screen.getByText('—')).toBeInTheDocument();
});

test('保存进行中阻止重复创建', async () => {
  let resolveSave: (() => void) | undefined;
  const onSave = jest.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveSave = resolve;
      }),
  );
  render(
    <RoutinePanel
      routines={[]}
      editable
      onLoadModels={loadModels}
      onSave={onSave}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '新建任务' }));
  fireEvent.change(screen.getByLabelText('任务名称'), { target: { value: '日报' } });
  fireEvent.change(screen.getByLabelText('执行指令'), { target: { value: '生成日报' } });
  const save = screen.getByRole('button', { name: '保存' });
  fireEvent.click(save);
  fireEvent.click(save);

  expect(onSave).toHaveBeenCalledTimes(1);
  expect(save).toBeDisabled();
  resolveSave?.();
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

test('打开创建表单时按需加载模型，并展示模型与超时时间配置', async () => {
  render(
    <RoutinePanel
      routines={[]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(loadModels).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '新建任务' }));

  await waitFor(() => expect(loadModels).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText('执行模型')).toBeInTheDocument();
  expect(screen.getByLabelText('超时时间（秒）')).toHaveValue(1800);
});

test('compact 时任务卡片退回单列，否则多列栅格', () => {
  const gridClassName = (ui: { container: HTMLElement }) =>
    ui.container.querySelector('.grid')?.className ?? '';

  const expanded = render(
    <RoutinePanel
      routines={[routine]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );
  expect(gridClassName(expanded)).toContain('xl:grid-cols-3');

  const compact = render(
    <RoutinePanel
      routines={[routine]}
      editable
      compact
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );
  expect(gridClassName(compact)).not.toContain('grid-cols-3');
  expect(gridClassName(compact)).not.toContain('grid-cols-2');
});

test('卡片用 #F8F8F8 画布底色，内容与操作药丸之间有分割线', () => {
  const { container } = render(
    <RoutinePanel
      routines={[routine]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  const card = container.querySelector('section');
  expect(card?.className).toContain('bg-page-canvas');

  // foot 与 content 之间的分割线：编辑/删除药丸所在行带上边框
  const footerRow = screen.getByRole('button', { name: '编辑晨报' }).closest('div');
  expect(footerRow?.className).toContain('border-t');
});

test('三字段按稿面顺序一行分布，删除红字、卡片有 hover 投影', () => {
  const { container } = render(
    <RoutinePanel
      routines={[routine]}
      editable
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  // 字段顺序：执行频率 → 下次执行 → 执行配置
  const fields = ['执行频率', '下次执行', '执行配置'].map((label) => screen.getByText(label));
  expect(
    fields[0].compareDocumentPosition(fields[1]) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    fields[1].compareDocumentPosition(fields[2]) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();

  // 三字段一行分散排布（窄卡 8px 间距 / 宽卡 ~111px 分散，同一 justify-between 语义）
  expect(container.querySelector('dl')?.className).toContain('justify-between');

  // 删除按钮红字 + 红 hover 底；编辑保持灰字
  expect(screen.getByRole('button', { name: '删除晨报' }).className).toContain('text-destructive');
  expect(screen.getByRole('button', { name: '编辑晨报' }).className).toContain('text-zinc-600');

  // 卡片 hover 浮起
  expect(container.querySelector('section')?.className).toContain('hover:shadow-sm');
});

test('启停中的任务显示等待提示并锁定开关', () => {
  render(
    <RoutinePanel
      routines={[routine]}
      editable
      pendingRoutineToggle={{ id: 'routine-1', enabled: false, slow: false }}
      onLoadModels={loadModels}
      onSave={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(screen.getByRole('status')).toHaveTextContent('正在停用…');
  expect(screen.getByRole('switch')).toBeDisabled();
});
