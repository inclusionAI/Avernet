import { extendCapabilities } from '@/capabilities';
import { getMergedNavigationItems } from '@/shell/navigation';
import { Compass } from 'lucide-react';

it('an overlay can explicitly place a generic item after the baseline tail', () => {
  extendCapabilities({
    getInternalNavigationItems: () => ({
      status: 'available',
      value: [
        {
          id: 'custom-tail',
          label: 'Custom',
          path: '/custom',
          icon: Compass,
          section: 'bot',
          description: '',
          placement: 'last',
        },
      ],
    }),
  });
  const items = getMergedNavigationItems().filter((item) => item.section === 'bot');
  expect(items.map((item) => item.id)).toEqual(['bot-workshop', 'evolution-factory', 'custom-tail']);
});
