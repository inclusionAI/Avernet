/** @jest-environment jsdom */
import { describe, expect, it, jest } from '@jest/globals';
import { render, screen } from '@testing-library/react';

jest.mock('@/components/ExternalBots/ExternalBotPanel', () => ({
  ExternalBotPanel: () => <div data-testid="external-bot-panel" />,
}));

import ExternalBotsPage from '@/pages/ExternalBots';

describe('ExternalBotsPage', () => {
  it('渲染薄壳：页头 + 复用的外部 Bot 面板', () => {
    render(<ExternalBotsPage />);
    expect(screen.getByText('外部 Bot')).toBeTruthy();
    expect(screen.getByTestId('external-bot-panel')).toBeTruthy();
  });
});
