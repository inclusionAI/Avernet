/** @jest-environment jsdom */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CaseSessionSource } from '../src/pages/EvolutionFactory/CaseSessionSource';

it('shows and copies complete source identity', async () => {
  const writeText = jest.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<CaseSessionSource sourceSessionId="full-session-id" sourceSessionKey="agent:main:session:source:user:example" />);
  fireEvent.click(screen.getByRole('button', { name: '复制来源 Session ID' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('full-session-id'));
  fireEvent.click(screen.getByRole('button', { name: '复制来源 Session Key' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('agent:main:session:source:user:example'));
});

it('keeps legacy cases readable without invented identity or copy controls', () => {
  render(<CaseSessionSource />);
  expect(screen.getAllByText('未记录')).toHaveLength(2);
  expect(screen.queryByRole('button')).toBeNull();
});

it('reports clipboard failure without hiding the source', async () => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: jest.fn().mockRejectedValue(new Error('denied')) } });
  render(<CaseSessionSource sourceSessionId="full-session-id" />);
  fireEvent.click(screen.getByRole('button', { name: '复制来源 Session ID' }));
  await screen.findByText('复制失败，请手动复制');
  expect(screen.getByText('full-session-id')).toBeTruthy();
});
