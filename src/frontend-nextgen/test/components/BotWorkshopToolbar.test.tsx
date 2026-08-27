/** @jest-environment jsdom */
import BotWorkshopToolbar from '@/components/BotWorkshop/BotWorkshopToolbar';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

it('服务类型下拉提供可清除条件的默认项', async () => {
  const onServiceModeChange = jest.fn();
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
  render(
    <BotWorkshopToolbar
      keyword=""
      engine=""
      serviceMode="service"
      onKeywordChange={jest.fn()}
      onEngineChange={jest.fn()}
      onDeploymentChange={jest.fn()}
      onServiceModeChange={onServiceModeChange}
      onCreateCloud={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByRole('combobox', { name: '服务类型' }));

  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['服务类型', '服务化', '非服务化']);
  await userEvent.click(screen.getByRole('option', { name: '服务类型' }));
  expect(onServiceModeChange).toHaveBeenCalledWith(undefined);
});
