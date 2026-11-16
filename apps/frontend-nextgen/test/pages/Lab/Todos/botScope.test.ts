import type { Space } from '@/domain/admin/models';
import { botBelongsToCurrentSpace, eligibleTodoBots } from '@/pages/Lab/Todos/botScope';

const personalSpace: Space = {
  spaceId: 10,
  spaceCode: 'personal-u-1',
  spaceName: '个人空间',
  spaceType: 'PERSONAL',
  currentUserRole: 'ADMIN',
  memberCount: 1,
  ownerCount: 1,
  botCount: 1,
  gmtModified: '2026-11-10T01:00:00Z',
};

const teamSpace: Space = { ...personalSpace, spaceType: 'TEAM' };

describe('Lab Todos Bot scope', () => {
  it('personal space only accepts the current human own bot', () => {
    const ownBot = { id: 'bot-1', name: '我的 Bot', ownerId: 'u-1' };
    const foreignBot = { id: 'bot-2', name: '他人 Bot', ownerId: 'u-2' };
    expect(botBelongsToCurrentSpace(ownBot, personalSpace, 'u-1')).toBe(true);
    expect(botBelongsToCurrentSpace(foreignBot, personalSpace, 'u-1')).toBe(false);
  });

  it('rejects personal bots before the current identity is ready', () => {
    expect(botBelongsToCurrentSpace({ id: 'bot-1', ownerId: 'u-1' }, personalSpace, undefined)).toBe(false);
  });

  it('team space keeps all current-space bots from the inventory', () => {
    const publicCatalogIds = new Set(['bot-1:u-1']);
    const options = eligibleTodoBots(
      [
        { id: 'bot-1', name: 'One', ownerId: 'u-1' },
        { id: 'bot-2', name: 'Two', ownerId: 'u-2' },
      ],
      teamSpace,
      'u-1',
      publicCatalogIds,
    );
    expect(options).toEqual([{ botId: 'bot-1', botName: 'One' }]);
  });

  it('personal space requires both current-human ownership and public collaboration visibility', () => {
    const options = eligibleTodoBots(
      [
        { id: 'bot-1', name: 'Public', ownerId: 'u-1' },
        { id: 'bot-2', name: 'Private', ownerId: 'u-1' },
        { id: 'bot-3', name: 'Foreign', ownerId: 'u-2' },
      ],
      personalSpace,
      'u-1',
      new Set(['bot-1:u-1', 'bot-3:u-2']),
    );
    expect(options).toEqual([{ botId: 'bot-1', botName: 'Public' }]);
  });

  it('unknown spaces fail closed', () => {
    expect(eligibleTodoBots([{ id: 'bot-1' }], undefined, 'u-1', new Set())).toEqual([]);
  });
});
