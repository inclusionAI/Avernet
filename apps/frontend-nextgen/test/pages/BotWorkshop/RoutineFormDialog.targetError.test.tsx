/** @jest-environment jsdom */
import { RoutineFormDialog } from '@/pages/BotWorkshop/ScheduledTasks/RoutineFormDialog';
import type { ManagedRoutine, RoutineContext } from '@/services/botScheduledTasks/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

// 创建弹窗与目标相关的失败原因必须始终可见：Caller 配置未确认等与表单填写无关的
// 失败会把保存按钮置灰，用户需要看到原因；未取得实例清单的环境不再拦截保存，改为
// 提示由服务端解析并在失败时透传后端阶段化消息。
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

it('草稿态未取得实例清单时提示由服务端解析，保存可用', () => {
  renderDialog(buildContext({ engine: 'teclaw', caller_mode: false }));
  fillForm();
  fireEvent.click(screen.getByRole('combobox', { name: '生效环境' }));
  fireEvent.click(screen.getByRole('option', { name: '草稿态' }));
  expect(screen.getByText(/保存后由服务端解析目标/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
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

it('多实例环境需先勾选运行实例，勾选后才能保存', () => {
  renderDialog(
    buildContext({
      engine: 'openclaw',
      environments: [
        { runtime_stage: 'draft', instances: [] },
        { runtime_stage: 'verify', instances: [] },
        { runtime_stage: 'online', instances: ['uuid-a', 'uuid-b'] },
      ],
    }),
  );
  fillForm();
  fireEvent.click(screen.getByRole('combobox', { name: '生效环境' }));
  fireEvent.click(screen.getByRole('option', { name: '运行中' }));
  expect(screen.getByText('请选择当前环境中已确认的运行实例')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  fireEvent.pointerDown(screen.getByRole('button', { name: '运行实例' }));
  fireEvent.click(screen.getByRole('button', { name: '运行实例' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'uuid-a' }));
  expect(screen.getByText('已选 1 个实例')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
});

it('编辑任务时运行实例保持固定，不出现多选', () => {
  const original: ManagedRoutine = {
    id: 'job/a',
    key: JSON.stringify(['u1', 'b1', 'online', 'uuid-a', null, 'job/a']),
    bot_id: 'b1',
    name: '每日站会日报',
    cron: '0 9 * * *',
    command: '总结今天群里的重点讨论',
    enabled: true,
    timezone: 'Asia/Shanghai',
    timeoutSecs: 1800,
    runtime_stage: 'online',
    device_uuid: 'uuid-a',
  };
  render(
    <RoutineFormDialog
      context={buildContext({
        engine: 'openclaw',
        environments: [
          { runtime_stage: 'draft', instances: [] },
          { runtime_stage: 'verify', instances: [] },
          { runtime_stage: 'online', instances: ['uuid-a', 'uuid-b'] },
        ],
      })}
      original={original}
      editable
      onClose={jest.fn()}
      onSave={jest.fn(async () => undefined)}
      onLoadModels={() => Promise.resolve([])}
    />,
  );
  expect(screen.getByRole('combobox', { name: '运行实例' })).toBeDisabled();
  expect(screen.getByText(/执行目标已固定/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '运行实例' })).not.toBeInTheDocument();
});
