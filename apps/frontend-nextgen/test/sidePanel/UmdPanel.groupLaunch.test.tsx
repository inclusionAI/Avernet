/** @jest-environment jsdom */
import { UmdPanel } from '@/services/bcs/UmdPanel';
import { resolveUmdComponent } from '@/services/bcs/resolveUmdModule';
import { useCollaborationGroupLaunchStore } from '@/stores/collaborationGroupLaunchStore';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent/registry';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({ registerPanelContent: jest.fn() }));
jest.mock('@/services/bcs/resolveUmdModule');
const draft = { schemaVersion: 2, draftId: '12345678-1234-1234-1234-123456789abc', revision: 1 };

it('injects the host callback and V1 base, preserves repeated launches and closes on source change', async () => {
  useCollaborationGroupLaunchStore.setState({ request: null, busy: false });
  const ignoredCallback = jest.fn();
  const remote = jest.fn(({ onOpenCreateGroup }: { onOpenCreateGroup?: (options: unknown) => void }) => (
    <button type="button" onClick={() => onOpenCreateGroup?.({ draft })}>
      发起协作
    </button>
  ));
  jest.mocked(resolveUmdComponent).mockResolvedValue(remote);
  const tab = {
    id: 'preview-1',
    params: {
      cdn: '/panel.js',
      entry: 'bcsPanel.CollaborationDefinitionPreview',
      data: { params: draft, onOpenCreateGroup: ignoredCallback },
    },
  };
  const { rerender, unmount } = render(<UmdPanel {...({ tab } as unknown as PanelContentProps)} />);
  fireEvent.click(await screen.findByText('发起协作'));
  const request = useCollaborationGroupLaunchStore.getState().request;
  expect(request?.options).toEqual({ draft });
  expect(remote.mock.calls[0][0]).toMatchObject({ apiBaseUrl: '/api/v1/collaboration' });
  fireEvent.click(screen.getByText('发起协作'));
  expect(useCollaborationGroupLaunchStore.getState().request).toBe(request);
  expect(ignoredCallback).not.toHaveBeenCalled();
  rerender(<UmdPanel {...({ tab: { ...tab, id: 'preview-2' } } as unknown as PanelContentProps)} />);
  expect(useCollaborationGroupLaunchStore.getState().request).toBeNull();
  fireEvent.click(await screen.findByText('发起协作'));
  expect(useCollaborationGroupLaunchStore.getState().request).not.toBeNull();
  unmount();
  expect(useCollaborationGroupLaunchStore.getState().request).toBeNull();
});

it('keeps an in-flight submission mounted when its source closes or another panel launches', () => {
  const store = useCollaborationGroupLaunchStore.getState();
  store.open('first', { draft });
  const original = useCollaborationGroupLaunchStore.getState().request;
  store.setBusy(true);
  store.close('first');
  store.open('second', { draft });
  expect(useCollaborationGroupLaunchStore.getState().request).toBe(original);
  store.setBusy(false);
  store.close();
});
