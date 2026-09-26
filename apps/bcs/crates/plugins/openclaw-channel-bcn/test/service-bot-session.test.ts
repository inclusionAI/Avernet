import { strict as assert } from 'node:assert';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  ensureServiceBotSession,
  isServiceBot,
  loadServiceBotCredentials,
  parseServiceBotCredentials,
  resolveServiceBotCredentialsBotId,
} from '../src/service-bot-session.js';

describe('service bot session bootstrap', () => {
  let originalIgnoreCredentials: string | undefined;
  let originalBotType: string | undefined;

  beforeEach(() => {
    originalIgnoreCredentials = process.env.BCS_IGNORE_CREDENTIALS;
    originalBotType = process.env.BOT_TYPE;
    delete process.env.BCS_IGNORE_CREDENTIALS;
    delete process.env.BOT_TYPE;
  });

  afterEach(() => {
    if (originalIgnoreCredentials === undefined) delete process.env.BCS_IGNORE_CREDENTIALS;
    else process.env.BCS_IGNORE_CREDENTIALS = originalIgnoreCredentials;
    if (originalBotType === undefined) delete process.env.BOT_TYPE;
    else process.env.BOT_TYPE = originalBotType;
  });

  it('parses owner identity and legacy ENTITY_ID fallback', () => {
    const preferred = parseServiceBotCredentials([
      '# service identity',
      'BOT_TYPE = service',
      'BOT_ID=bot=value',
      'OWNER_ID=owner',
      'ENTITY_ID=legacy-owner',
    ].join('\n'));
    const legacy = parseServiceBotCredentials('BOT_ID=bot\nENTITY_ID=legacy-owner');

    assert.equal(preferred.botType, 'service');
    assert.equal(preferred.botUuid, 'bot=value:owner');
    assert.equal(legacy.botUuid, 'bot:legacy-owner');
  });

  it('loads credentials and degrades safely for missing files', () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcs-credentials-'));
    const credentialsPath = join(dataDir, '.credentials');
    try {
      writeFileSync(credentialsPath, 'BOT_ID=bot\nOWNER_ID=owner', 'utf-8');
      assert.equal(loadServiceBotCredentials(credentialsPath)?.botUuid, 'bot:owner');
      assert.equal(loadServiceBotCredentials(join(dataDir, 'missing')), null);
      assert.equal(resolveServiceBotCredentialsBotId(join(dataDir, 'missing')), undefined);
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('creates a service bot session with an injected account resolver', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcs-service-session-'));
    const credentialsPath = join(dataDir, '.credentials');
    writeFileSync(credentialsPath, 'BOT_TYPE=service\nBOT_ID=bot\nOWNER_ID=owner', 'utf-8');

    try {
      const result = await ensureServiceBotSession({
        dataDir,
        credentialsPath,
        cfg: {},
        resolveAccount: () => ({ bcsUrl: 'wss://internal.example/ws/bot' } as any),
      });

      assert.equal(result.created, true);
      assert.deepEqual(JSON.parse(readFileSync(join(dataDir, '.bcs', 'session.json'), 'utf-8')), {
        bot_uuid: 'bot:owner',
        token: 'dummy',
        bcs_url: 'wss://internal.example/ws/bot',
      });
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('does not overwrite an existing service bot session', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcs-existing-session-'));
    const credentialsPath = join(dataDir, '.credentials');
    const sessionPath = join(dataDir, '.bcs', 'session.json');
    const originalSession = { bot_uuid: 'old:owner', token: 'old-token', bcs_url: 'wss://old.example' };
    writeFileSync(credentialsPath, 'BOT_TYPE=service\nBOT_ID=bot\nOWNER_ID=owner', 'utf-8');
    mkdirSync(join(dataDir, '.bcs'), { recursive: true });
    writeFileSync(sessionPath, JSON.stringify(originalSession), 'utf-8');

    try {
      const result = await ensureServiceBotSession({ dataDir, credentialsPath });
      assert.equal(result.reason, 'session_exists');
      assert.deepEqual(JSON.parse(readFileSync(sessionPath, 'utf-8')), originalSession);
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('uses environment BOT_TYPE only when credentials omit BOT_TYPE', () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcs-service-type-'));
    const credentialsPath = join(dataDir, '.credentials');
    process.env.BOT_TYPE = 'service';

    try {
      writeFileSync(credentialsPath, 'BOT_ID=bot\nOWNER_ID=owner', 'utf-8');
      assert.equal(isServiceBot(credentialsPath), true);
      writeFileSync(credentialsPath, 'BOT_TYPE=personal\nBOT_ID=bot\nOWNER_ID=owner', 'utf-8');
      assert.equal(isServiceBot(credentialsPath), false);
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('BCS_IGNORE_CREDENTIALS disables identity, detection, and bootstrap', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcs-ignore-credentials-'));
    const credentialsPath = join(dataDir, '.credentials');
    writeFileSync(credentialsPath, 'BOT_TYPE=service\nBOT_ID=bot\nOWNER_ID=owner', 'utf-8');
    process.env.BCS_IGNORE_CREDENTIALS = '1';

    try {
      assert.equal(resolveServiceBotCredentialsBotId(credentialsPath), undefined);
      assert.equal(isServiceBot(credentialsPath), false);
      assert.equal((await ensureServiceBotSession({ dataDir, credentialsPath })).reason, 'not_service_bot');
      assert.equal(existsSync(join(dataDir, '.bcs', 'session.json')), false);
    } finally {
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
});
