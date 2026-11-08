/** @jest-environment jsdom */
import { defaultCapabilities } from '@/capabilities/defaultCapabilities';
import type { BotEditorPanelExtension } from '@/capabilities/types';
import type { BotDomain } from '@/domain/botWorkshop';
import { validEditorPanels } from '@/pages/BotWorkshop/Detail/useBotEditorPanels';
import { useHostingTabs } from '@/pages/BotWorkshop/Detail/useHostingTabs';
import { act, renderHook } from '@testing-library/react';
jest.mock('@/hooks/useDwsEligibility', () => ({ useDwsEligibility: () => false }));
const Panel = () => null;
const extension = (id: string): BotEditorPanelExtension => ({ id, label: id, Icon: Panel, Panel });
it('public default has no panels', () => {
  expect(defaultCapabilities.getBotEditorPanels()).toEqual({ status: 'available', value: [] });
});
it('rejects reserved, duplicate and invalid IDs', () => {
  expect(
    validEditorPanels(
      ['resource', 'capability', 'routine', 'escort', 'hosting', '', 'Invalid', 'custom', 'custom'].map(extension),
    ).map((p) => p.id),
  ).toEqual(['custom']);
});
it('retains ordinary tabs with no extensions and falls back when an extension disappears', () => {
  const bot = { ownerId: 'owner', runtime: {} } as BotDomain;
  const extra = [{ key: 'extension:custom' as const, label: 'Custom', icon: null }];
  const { result, rerender } = renderHook(
    ({ extensions }) => useHostingTabs('b', 'owner', bot, true, true, extensions),
    { initialProps: { extensions: extra } },
  );
  act(() => result.current.setTab('resource'));
  expect(result.current.tab).toBe('resource');
  act(() => result.current.setTab('extension:custom'));
  expect(result.current.tab).toBe('extension:custom');
  rerender({ extensions: [] });
  expect(result.current.tab).toBe('capability');
  expect(result.current.visibleMainTabs.map((t) => t.key)).not.toContain('extension:custom');
});
it.each(['bot', 'owner', 'context'])('resets selected panels on %s change', (change) => {
  const { result, rerender } = renderHook(
    ({ id, owner, context }) =>
      useHostingTabs(
        id,
        'actor',
        { ownerId: owner, runtime: {} } as BotDomain,
        true,
        true,
        [{ key: 'extension:custom', label: 'Custom', icon: null }],
        context,
      ),
    { initialProps: { id: 'b', owner: 'o', context: 'draft' } },
  );
  act(() => result.current.setTab('extension:custom'));
  rerender({
    id: change === 'bot' ? 'other' : 'b',
    owner: change === 'owner' ? 'other' : 'o',
    context: change === 'context' ? 'online' : 'draft',
  });
  expect(result.current.tab).toBe('capability');
});
