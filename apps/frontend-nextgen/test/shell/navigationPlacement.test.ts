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
  expect(items.map((item) => item.id)).toEqual(['bot-workshop', 'task-escort', 'evolution-factory', 'custom-tail']);
});

it('ordinary overlays precede the adjacent management tail pair while explicit last items keep their order', () => {
  extendCapabilities({
    getInternalNavigationItems: () => ({
      status: 'available',
      value: [
        {
          id: 'first-tail',
          label: 'First tail',
          path: '/first-tail',
          icon: Compass,
          section: 'bot',
          description: '',
          placement: 'last',
        },
        {
          id: 'capability-workshop',
          label: '能力管理',
          path: '/capability-workshop',
          icon: Compass,
          section: 'bot',
          description: '',
        },
        {
          id: 'collab-tail',
          label: 'Collab tail',
          path: '/collab-tail',
          icon: Compass,
          section: 'collab',
          description: '',
          placement: 'last',
        },
        { id: 'market', label: '能力市场', path: '/market', icon: Compass, section: 'bot', description: '' },
        {
          id: 'second-tail',
          label: 'Second tail',
          path: '/second-tail',
          icon: Compass,
          section: 'bot',
          description: '',
          placement: 'last',
        },
      ],
    }),
  });
  const items = getMergedNavigationItems();
  expect(items.map((item) => item.id)).toEqual([
    'conversation',
    'collaboration',
    'collaboration-square',
    'lab',
    'collab-tail',
    'bot-workshop',
    'capability-workshop',
    'market',
    'task-escort',
    'evolution-factory',
    'first-tail',
    'second-tail',
  ]);
  expect(getMergedNavigationItems()).toEqual(items);
});
