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

  it('renders toast messages at the top center of the page', () => {
    renderToStaticMarkup(<Toaster />);

    expect(mockSonnerToaster).toHaveBeenCalledWith(
      expect.objectContaining({
        position: 'top-center',
        richColors: true,
        closeButton: true,
      }),
    );
  });
});
