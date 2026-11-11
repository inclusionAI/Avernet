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
    const options = eligibleTodoBots(
      [
        { id: 'bot-1', name: 'One', ownerId: 'u-1' },
        { id: 'bot-2', name: 'Two', ownerId: 'u-2' },
      ],
      teamSpace,
      'u-1',
    );
    expect(options).toEqual([
      { botId: 'bot-1', botName: 'One' },
      { botId: 'bot-2', botName: 'Two' },
    ]);
  });

  it('unknown spaces fail closed', () => {
    expect(eligibleTodoBots([{ id: 'bot-1' }], undefined, 'u-1')).toEqual([]);
  });
});
