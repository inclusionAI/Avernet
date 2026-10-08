/** @jest-environment jsdom */
import { MoreConfigPanel } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

test('引擎配置支持确认后恢复默认配置', async () => {
  const user = userEvent.setup();
  const onRestoreDefaults = jest.fn().mockResolvedValue(undefined);
  render(
    <MoreConfigPanel
      tab="engine"
      config={{ model: 'custom' }}
      editable
      restoringDefaults={false}
      onConfigChange={jest.fn()}
      onSave={jest.fn()}
      onRestoreDefaults={onRestoreDefaults}
    />,
  );

  await user.click(screen.getByRole('button', { name: '恢复默认配置' }));
  expect(screen.getByText('确认恢复默认配置？')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '确认恢复' }));

  expect(onRestoreDefaults).toHaveBeenCalledTimes(1);
});
