import { formatAutoGroupName } from '@/pages/Workspace/components/Modals/groupNaming';

const nameById = new Map([
  ['b1', 'Alpha'],
  ['b2', 'Beta'],
  ['b3', 'Gamma'],
  ['b4', 'Delta'],
  ['b5', 'Epsilon'],
  ['b6', 'Zeta'],
]);

it('joins five or fewer participant names', () => {
  expect(formatAutoGroupName(['b1', 'b2', 'b3'], 'b3', false, (id) => nameById.get(id) ?? id)).toBe(
    'Alpha、Beta、Gamma',
  );
});

it('truncates auto group names after five participants', () => {
  expect(formatAutoGroupName(['b1', 'b2', 'b3', 'b4', 'b5', 'b6'], 'b6', false, (id) => nameById.get(id) ?? id)).toBe(
    'Alpha、Beta、Gamma、Delta、Epsilon等',
  );
});
