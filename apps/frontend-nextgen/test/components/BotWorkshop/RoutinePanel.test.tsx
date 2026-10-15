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

test('任务卡片按摘要布局展示更新时间、频率、下次执行和执行配置，不展示 Prompt', () => {
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
  expect(screen.getByRole('button', { name: '编辑晨报' })).toHaveTextContent('编辑');
  expect(screen.getByRole('button', { name: '删除晨报' })).toHaveTextContent('删除');
  expect(screen.queryByText('整理昨日进展')).not.toBeInTheDocument();
});

test('后端未返回下次执行时间时不渲染该字段', () => {
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

  expect(screen.queryByText('下次执行')).not.toBeInTheDocument();
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
