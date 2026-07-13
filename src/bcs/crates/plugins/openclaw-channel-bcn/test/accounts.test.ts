import { strict as assert } from 'node:assert';
import { resolveAccount } from '../src/accounts.js';

describe('BCS account resolution', () => {
  it('marks configured bot ids as explicit connect identities', () => {
    const cfg = {
      channels: {
        bcs: {
          bcsUrl: 'ws://localhost:21000/ws/bot',
          botId: 'primary-bot',
          accounts: {
            secondary: {
              botId: 'secondary-bot',
            },
          },
        },
      },
    };

    assert.equal(resolveAccount(cfg).connectBotId, 'primary-bot');
    assert.equal(resolveAccount(cfg, 'secondary').connectBotId, 'secondary-bot');
  });

  it('does not use the default bot id as a configured connect identity', () => {
    const originalBcsBotId = process.env.BCS_BOT_ID;
    delete process.env.BCS_BOT_ID;

    try {
      const account = resolveAccount({});
      assert.equal(account.botId, 'openclaw-bot');
      assert.equal(account.connectBotId, undefined);
    } finally {
      if (originalBcsBotId === undefined) {
        delete process.env.BCS_BOT_ID;
      } else {
        process.env.BCS_BOT_ID = originalBcsBotId;
      }
    }
  });

  it('uses BCS_BOT_ID as an explicit connect identity', () => {
    const originalBcsBotId = process.env.BCS_BOT_ID;
    process.env.BCS_BOT_ID = 'env-bot';

    try {
      const account = resolveAccount({});
      assert.equal(account.botId, 'env-bot');
      assert.equal(account.connectBotId, 'env-bot');
    } finally {
      if (originalBcsBotId === undefined) {
        delete process.env.BCS_BOT_ID;
      } else {
        process.env.BCS_BOT_ID = originalBcsBotId;
      }
    }
  });
});
