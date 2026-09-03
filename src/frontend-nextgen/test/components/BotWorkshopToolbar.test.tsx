/** @jest-environment jsdom */
import BotWorkshopToolbar from '@/components/BotWorkshop/BotWorkshopToolbar';
import { fireEvent, render, screen } from '@testing-library/react';

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
      onReset={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('combobox', { name: '服务类型' }));

  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['服务类型', '服务化', '非服务化']);
  fireEvent.click(screen.getByRole('option', { name: '服务类型' }));
  expect(onServiceModeChange).toHaveBeenCalledWith(undefined);
});

it('引擎筛选下拉（Open Core 形态）提供 Claude Code 原生入口', async () => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
  render(
    <BotWorkshopToolbar
      keyword=""
      engine=""
      onKeywordChange={jest.fn()}
      onEngineChange={jest.fn()}
      onDeploymentChange={jest.fn()}
      onServiceModeChange={jest.fn()}
      onCreateCloud={jest.fn()}
      onReset={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('combobox', { name: '引擎类型' }));

  expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
    '引擎类型',
    'OpenClaw',
    'Claudecode引擎-原生',
  ]);
  expect(screen.queryByRole('option', { name: 'AgentCoding' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'Hermes' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'TEClaw' })).toBeNull();
});
