import { renderToStaticMarkup } from 'react-dom/server';

const mockSonnerToaster = jest.fn<null, [unknown]>(() => null);

jest.mock('sonner', () => ({
  Toaster: (props: unknown) => mockSonnerToaster(props),
}));

import { Toaster } from '@/components/ui/sonner';

describe('global Toaster', () => {
  beforeEach(() => {
    mockSonnerToaster.mockClear();
  });

  it('renders toast messages at the top center of the page without a close button', () => {
    renderToStaticMarkup(<Toaster />);

    const props = mockSonnerToaster.mock.calls[0][0] as Record<string, unknown>;
    expect(props).toEqual(
      expect.objectContaining({
        position: 'top-center',
        richColors: true,
      }),
    );
    expect(props.closeButton).toBeUndefined();
  });
});
