/** @jest-environment jsdom */
import { RoutineFormDialog } from '@/pages/BotWorkshop/ScheduledTasks/RoutineFormDialog';
import type { RoutineContext } from '@/services/botScheduledTasks/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

// 创建弹窗的目标校验错误必须始终可见：否则“无可用实例 / Caller 配置未确认”这类
// 与表单填写无关的失败会把保存按钮永久置灰，用户却看不到任何原因。
const buildContext = (overrides: Partial<RoutineContext>): RoutineContext => ({
  bot_id: 'b1',
  bot_name: 'Bot',
  owner_id: 'u1',
  bot_type: 'service',
  engine: 'teclaw',
  caller_mode: false,
  supported: true,
  can_manage: true,
  environments: [
    { runtime_stage: 'draft', instances: [] },
    { runtime_stage: 'verify', instances: [] },
    { runtime_stage: 'online', instances: [] },
  ],
  ...overrides,
});
const renderDialog = (context: RoutineContext) =>
  render(
    <RoutineFormDialog
      context={context}
      editable
      onClose={jest.fn()}
      onSave={jest.fn(async () => undefined)}
      onLoadModels={() => Promise.resolve([])}
    />,
  );
const fillForm = () => {
  fireEvent.change(screen.getByLabelText('任务名称'), { target: { value: '每日站会日报' } });
  fireEvent.change(screen.getByLabelText('执行指令'), { target: { value: '总结今天群里的重点讨论' } });
};

beforeEach(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

it('草稿态尚无实例时显示无法保存的原因，保存保持禁用', () => {
  renderDialog(buildContext({ engine: 'teclaw', caller_mode: false }));
  fillForm();
  fireEvent.click(screen.getByRole('combobox', { name: '生效环境' }));
  fireEvent.click(screen.getByRole('option', { name: '草稿态' }));
  expect(screen.getByText('当前环境尚无可用运行实例，请确认实例已就绪后刷新')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
});

it('Caller 配置未确认时弹窗立即说明原因，而不是沉默禁用', () => {
  renderDialog(buildContext({ caller_mode: undefined }));
  expect(screen.getByText('尚未确认 Bot 的 Caller 配置，请刷新后重试')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
});

it('teclaw Caller 任务默认在线环境，填完字段即可保存', () => {
  renderDialog(buildContext({ caller_mode: true }));
  fillForm();
  expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
});

it('个人 Bot 不做服务化目标校验，填完字段即可保存', () => {
  renderDialog(buildContext({ bot_type: 'personal', caller_mode: false }));
  fillForm();
  expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
});
