/** @jest-environment jsdom */

import { DetailNavigation } from '@/pages/BotWorkshop/Detail/DetailNavigation';
import { mainTabs } from '@/pages/BotWorkshop/Detail/detailTabs';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

test('左侧模块栏默认只展示前两个字，悬浮后展示完整名称', async () => {
  render(
    <DetailNavigation
      mainTabs={[{ key: 'routine', label: '定时任务', icon: <span /> }]}
      moreTabs={[]}
      activeTab="routine"
      onTabChange={jest.fn()}
      onMoreChange={jest.fn()}
    />,
  );

  const routine = screen.getByRole('button', { name: '定时任务' });
  expect(routine).toHaveTextContent('定时');
  expect(routine).not.toHaveTextContent('定时任务');

  fireEvent.focus(routine);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('定时任务');
});

test('消息托管显示托管简称，保留完整提示和点击行为', async () => {
  const onTabChange = jest.fn();
  render(
    <DetailNavigation
      mainTabs={mainTabs}
      moreTabs={[]}
      activeTab="capability"
      onTabChange={onTabChange}
      onMoreChange={jest.fn()}
    />,
  );

  const hosting = screen.getByRole('button', { name: '消息托管' });
  expect(hosting).toHaveTextContent(/^托管$/);
  expect(screen.getByRole('button', { name: '定时任务' })).toHaveTextContent(/^定时$/);
  fireEvent.click(hosting);
  expect(onTabChange).toHaveBeenCalledWith('hosting');
  fireEvent.focus(hosting);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('消息托管');
});
