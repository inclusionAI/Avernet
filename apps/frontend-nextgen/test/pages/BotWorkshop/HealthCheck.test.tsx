/** @jest-environment jsdom */
import BotHealthCheckPage from '@/pages/BotWorkshop/HealthCheck';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@umijs/max', () => ({
  useLocation: () => ({ search: '?id=bot-1' }),
  history: { push: jest.fn() },
}));

jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopRequestIdentity: () => ({ ready: true, loading: false, error: undefined, userId: 'u1' }),
}));

jest.mock('@/hooks/useBotWorkshopDetail', () => ({
  useBotWorkshopDetail: () => ({
    bot: {
      id: 'bot-1',
      name: '测试 Bot',
      runtime: { engine: 'openclaw', visibleInOpenCore: true },
      deployment: 'cloud',
    },
    loading: false,
    error: undefined,
    capability: {
      dimensions: ['configuration'],
      showRadar: false,
      showLogDetails: false,
      showRawSnapshot: false,
    },
  }),
}));

jest.mock('@/hooks/useBotHealthCheck', () => ({
  useBotHealthCheck: () => ({
    summary: undefined,
    loading: false,
    checking: false,
    error: undefined,
    refresh: jest.fn(),
    openHealthCheck: jest.fn(),
    target: undefined,
  }),
}));

jest.mock('@/services/botHealthCheck', () => ({
  botHealthCheckService: {
    toTarget: () => ({ botId: 'bot-1', userId: 'u1', context: {} }),
  },
}));

it('以编辑页一致的头像、名称、返回按钮渲染健康检查页', () => {
  render(<BotHealthCheckPage />);

  expect(screen.getByLabelText('返回 Bot 工坊')).toBeInTheDocument();
  expect(screen.getByText('测试 Bot')).toBeInTheDocument();
});
