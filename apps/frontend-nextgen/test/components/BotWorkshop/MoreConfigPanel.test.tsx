/** @jest-environment jsdom */
import { MoreConfigPanel } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// CodeMirror 懒加载在 jsdom 中替换为受控 textarea，驱动 onChange 即可测试草稿逻辑
jest.mock('@/components/BotWorkshop/Editor/JsonCodeEditor', () => ({
  __esModule: true,
  JsonCodeEditor: ({ value, onChange, editable }: { value: string; onChange?: (value: string) => void; editable: boolean }) => (
    <textarea
      aria-label="引擎 JSON 配置"
      value={value}
      disabled={!editable}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

function renderEnginePanel(props: { config: Record<string, unknown>; savedConfig: Record<string, unknown>; onSave?: jest.Mock }) {
  return render(
    <MoreConfigPanel
      tab="engine"
      config={props.config}
      savedConfig={props.savedConfig}
      editable
      restoringDefaults={false}
      onConfigChange={jest.fn()}
      onSave={props.onSave ?? jest.fn().mockResolvedValue(undefined)}
      onRestoreDefaults={jest.fn().mockResolvedValue(undefined)}
    />,
  );
}

test('引擎配置支持确认后恢复默认配置', async () => {
  const user = userEvent.setup();
  const onRestoreDefaults = jest.fn().mockResolvedValue(undefined);
  render(
    <MoreConfigPanel
      tab="engine"
      config={{ model: 'custom' }}
      savedConfig={{ model: 'custom' }}
      editable
      restoringDefaults={false}
      onConfigChange={jest.fn()}
      onSave={jest.fn().mockResolvedValue(undefined)}
      onRestoreDefaults={onRestoreDefaults}
    />,
  );

  await user.click(screen.getByRole('button', { name: '恢复默认配置' }));
  expect(screen.getByText('确认恢复默认配置？')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '确认恢复' }));

  expect(onRestoreDefaults).toHaveBeenCalledTimes(1);
  expect(await screen.findByLabelText('引擎 JSON 配置')).toBeInTheDocument();
});

test('配置变更后悬停保存按钮弹出 diff 预览，确认保存后调用 onSave 并关闭气泡', async () => {
  const user = userEvent.setup();
  const onSave = jest.fn().mockResolvedValue(undefined);
  renderEnginePanel({ config: { model: 'new-model' }, savedConfig: { model: 'old-model' }, onSave });
  await screen.findByLabelText('引擎 JSON 配置');

  const saveButton = screen.getByRole('button', { name: '保存配置' });
  await user.hover(saveButton);

  expect(await screen.findByText('配置修改预览')).toBeInTheDocument();
  expect(screen.getByText('(1 处修改)')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认保存' })).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: '确认保存' }));
  expect(onSave).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(screen.queryByText('配置修改预览')).not.toBeInTheDocument());
});

test('保存接口失败时气泡保持打开且可重试', async () => {
  const user = userEvent.setup();
  const onSave = jest.fn().mockRejectedValue(new Error('保存失败'));
  renderEnginePanel({ config: { a: 1 }, savedConfig: { a: 2 }, onSave });
  await screen.findByLabelText('引擎 JSON 配置');

  const saveButton = screen.getByRole('button', { name: '保存配置' });
  await user.hover(saveButton);
  const confirm = await screen.findByRole('button', { name: '确认保存' });

  await user.click(confirm);
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(await screen.findByRole('button', { name: '确认保存' })).toBeInTheDocument();
});

test('无变更时保存按钮禁用且悬停不弹气泡', async () => {
  const user = userEvent.setup();
  const onSave = jest.fn().mockResolvedValue(undefined);
  renderEnginePanel({ config: { model: 'same' }, savedConfig: { model: 'same' }, onSave });
  await screen.findByLabelText('引擎 JSON 配置');

  const saveButton = screen.getByRole('button', { name: '保存配置' });
  expect(saveButton).toBeDisabled();

  await user.hover(saveButton);
  expect(await screen.findByLabelText('引擎 JSON 配置')).toBeInTheDocument();
  expect(screen.queryByText('配置修改预览')).not.toBeInTheDocument();
});
