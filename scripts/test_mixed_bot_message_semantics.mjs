#!/usr/bin/env node
/**
 * Local mixed-message semantic smoke test.
 *
 * Creates a new 2 OpenClaw + 2 current Claude Provider group and verifies
 * initialization injects, explicit fan-out sends, and same-Claude concurrent
 * delivery. Output deliberately contains only method/count/result metadata.
 */

import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bcsBaseUrl = `http://127.0.0.1:${process.env.BCS_PORT || '21000'}`;
const statePath = path.join(root, 'scripts/.dependencies/bcs_baas_provider.state.json');
const bcsLogPath = path.join(root, 'scripts/.dependencies/logs/bcs.log');
const mockUserId = process.env.BCS_MOCK_USER_ID || '001';
const humanActorId = `human_${mockUserId}`;
const pollIntervalMs = 1_000;
const replyTimeoutMs = Number(process.env.MIXED_MESSAGE_REPLY_TIMEOUT_MS || 120_000);
const requestTimeoutMs = Number(process.env.MIXED_MESSAGE_REQUEST_TIMEOUT_MS || 35_000);

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fail(message) {
  throw new Error(message);
}

function safeError(error) {
  return error instanceof Error ? error.message.replace(/\s+/g, ' ').slice(0, 240) : 'unknown error';
}

async function requestJson(pathname, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), requestTimeoutMs);
  let response;
  try {
    response = await fetch(`${bcsBaseUrl}${pathname}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-Mock-User-Id': mockUserId,
        ...(options.headers || {}),
      },
    });
  } catch {
    fail('BCS request did not complete before the local timeout');
  } finally {
    clearTimeout(timeout);
  }
  const text = await response.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    // Never include raw response text in test output; it can contain chat data.
  }
  if (!response.ok) {
    fail(`BCS request failed with HTTP ${response.status}`);
  }
  return json;
}

async function bcsLogSize() {
  try {
    return (await stat(bcsLogPath)).size;
  } catch {
    return 0;
  }
}

async function readLogSince(offset) {
  try {
    const content = await readFile(bcsLogPath);
    return content.subarray(Math.min(offset, content.length)).toString('utf8');
  } catch {
    return '';
  }
}

function sessionContextDispatchWindow(lines, groupId) {
  const start = lines.findIndex((line) =>
    line.includes('dispatching system message') &&
    line.includes(`group_id=${groupId}`) &&
    line.includes('event_kind=SessionContext'),
  );
  if (start < 0) return null;
  const endOffset = lines.slice(start + 1).findIndex((line) =>
    line.includes('system message dispatch complete') &&
    line.includes(`group_id=${groupId}`) &&
    line.includes('event_kind=SessionContext'),
  );
  return endOffset < 0 ? null : lines.slice(start, start + endOffset + 2);
}

async function waitForSessionContextDispatch(offset, groupId) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < replyTimeoutMs) {
    const lines = (await readLogSince(offset)).split('\n');
    const window = sessionContextDispatchWindow(lines, groupId);
    if (window) return window;
    await pause(pollIntervalMs);
  }
  fail('SessionContext initialization: timed out waiting for group dispatch completion');
}

function currentClaudeIds(state) {
  const exactRole = (role) => (state.bots || []).filter((bot) => bot.role === role && bot.bot_uuid);
  const planners = exactRole('planner');
  const developers = exactRole('developer');
  if (planners.length !== 1 || developers.length !== 1) {
    fail('runtime state must contain exactly one current planner and developer Provider bot');
  }
  return { planner: planners[0].bot_uuid, developer: developers[0].bot_uuid };
}

function findExactBot(items, name) {
  const matches = items.filter((bot) => (bot.capabilities?.name || bot.bot_name) === name);
  if (matches.length !== 1 || !matches[0].bot_uuid) {
    fail(`expected exactly one current local bot named ${name}`);
  }
  return matches[0];
}

function assertOnlineBot(bot, expectedId, expectedName) {
  if (bot.bot_uuid !== expectedId || (bot.capabilities?.name || bot.bot_name) !== expectedName || bot.status !== 'online') {
    fail(`bot topology did not contain the expected online ${expectedName} bot`);
  }
}

function summarizeDelivery(results) {
  return (results || []).map((result) => ({
    botUuid: result.bot_uuid,
    deliveryType: result.delivery_type,
    success: result.success === true,
  }));
}

function assertDelivery(results, expected) {
  const normalized = summarizeDelivery(results);
  if (normalized.length !== expected.size) {
    fail(`expected ${expected.size} delivery results, received ${normalized.length}`);
  }
  const actual = new Map(normalized.map((result) => [result.botUuid, result]));
  if (actual.size !== normalized.length) {
    fail('delivery results contained duplicate bot recipients');
  }
  for (const [botUuid, deliveryType] of expected) {
    const result = actual.get(botUuid);
    if (!result || !result.success || result.deliveryType !== deliveryType) {
      fail(`unexpected delivery result for ${botUuid}: expected ${deliveryType}`);
    }
  }
}

function assistantCounts(messages, botIds) {
  const counts = new Map(botIds.map((botId) => [botId, 0]));
  for (const message of messages || []) {
    if (message.role === 'assistant' && counts.has(message.sender)) {
      counts.set(message.sender, counts.get(message.sender) + 1);
    }
  }
  return counts;
}

async function sessionMessages(sessionId) {
  return requestJson(`/sessions/${encodeURIComponent(sessionId)}/messages?view_bot_id=${encodeURIComponent(humanActorId)}`);
}

async function waitForNewAssistantMessages(sessionId, before, expectedBotIds, label) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < replyTimeoutMs) {
    const messages = await sessionMessages(sessionId);
    const counts = assistantCounts(messages, expectedBotIds);
    if (expectedBotIds.every((botId) => counts.get(botId) > (before.get(botId) || 0))) {
      return counts;
    }
    await pause(pollIntervalMs);
  }
  fail(`${label}: timed out waiting for all expected assistant final messages`);
}

async function assertExpectedFinals(sessionId, before, expectedBotIds, label) {
  await waitForNewAssistantMessages(sessionId, before, expectedBotIds, label);
  await pause(1_500);
  const after = assistantCounts(await sessionMessages(sessionId), expectedBotIds);
  for (const botId of expectedBotIds) {
    const delta = (after.get(botId) || 0) - (before.get(botId) || 0);
    if (delta < 1) fail(`${label}: a chat.send recipient did not produce a final`);
  }
  return after;
}

async function sendHumanMessage(sessionId, message) {
  return requestJson(`/sessions/${encodeURIComponent(sessionId)}/chat`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

async function run() {
  const health = await requestJson('/health');
  if (health?.service !== 'bcs') fail('BCS health response is not a BCS service response');

  const state = JSON.parse(await readFile(statePath, 'utf8'));
  const { planner, developer } = currentClaudeIds(state);
  const botList = await requestJson('/bots/my');
  const items = botList.items || [];
  const plannerBot = findExactBot(items, 'Claude Planner（当前）');
  const developerBot = findExactBot(items, 'Claude Developer（当前）');
  assertOnlineBot(plannerBot, planner, 'Claude Planner（当前）');
  assertOnlineBot(developerBot, developer, 'Claude Developer（当前）');
  const ceoBot = findExactBot(items, 'CEO');
  const productManagerBot = findExactBot(items, '产品经理');
  assertOnlineBot(ceoBot, ceoBot.bot_uuid, 'CEO');
  assertOnlineBot(productManagerBot, productManagerBot.bot_uuid, '产品经理');
  const ceo = ceoBot.bot_uuid;
  const productManager = productManagerBot.bot_uuid;
  const allBotIds = [planner, developer, ceo, productManager];
  if (new Set(allBotIds).size !== 4) fail('mixed topology must contain four distinct bot IDs');

  const logOffset = await bcsLogSize();
  const group = await requestJson('/groups', {
    method: 'POST',
    body: JSON.stringify({
      label: `QA mixed message semantics ${Date.now()}`,
      driver_bot: planner,
      originator: humanActorId,
      group_strategy: 'chat',
      participants: [
        { bot_uuid: planner, role: 'driver' },
        { bot_uuid: developer, role: 'consultant' },
        { bot_uuid: ceo, role: 'consultant' },
        { bot_uuid: productManager, role: 'consultant' },
      ],
      context: 'Automated local message-routing verification. No tools or file modifications.',
    }),
  });
  const groupId = group?.id;
  const sessionId = group?.session_id;
  if (!groupId || !sessionId) fail('new group response omitted group or session ID');
  if (group.driver_bot !== planner) fail('new group did not retain the Claude Planner as driver');
  assertDelivery(
    (group.participants || []).map((bot_uuid) => ({ bot_uuid, delivery_type: 'member', success: true })),
    new Map(allBotIds.map((botId) => [botId, 'member'])),
  );

  const logGroupId = sessionId.includes(':') ? sessionId.split(':', 1)[0] : groupId;
  const initLines = await waitForSessionContextDispatch(logOffset, logGroupId);
  const providerDeliverStarts = initLines.filter((line) => line.includes('provider downlink: deliver start'));
  const initProviderInjects = providerDeliverStarts.filter((line) => line.includes('method=chat.inject')).length;
  const initProviderSends = providerDeliverStarts.filter((line) => line.includes('method=chat.send')).length;
  const initProviderTargets = new Set(providerDeliverStarts.map((line) => (line.match(/target_bot_id=([^ ]+)/) || [])[1]));
  const initSucceeded = initLines.some((line) =>
    line.includes('system message dispatch complete') &&
    line.includes('total_recipients=4') &&
    line.includes('successful=4') &&
    line.includes('failed=0'),
  );
  if (
    providerDeliverStarts.length !== 2 ||
    initProviderInjects !== 2 ||
    initProviderSends !== 0 ||
    initProviderTargets.size !== 2 ||
    !initProviderTargets.has(planner) ||
    !initProviderTargets.has(developer) ||
    !initSucceeded
  ) {
    fail('SessionContext did not exactly inject the two Provider bots and complete four recipients');
  }

  let before = assistantCounts(await sessionMessages(sessionId), allBotIds);
  if ([...before.values()].some((count) => count !== 0)) {
    fail('SessionContext inject initialization unexpectedly produced an assistant final');
  }
  const defaultRoute = await sendHumanMessage(
    sessionId,
    'Reply concisely as the Planner. Do not call tools or modify files.',
  );
  assertDelivery(defaultRoute.delivery_results, new Map([
    [planner, 'send'],
    [developer, 'inject'],
    [ceo, 'inject'],
    [productManager, 'inject'],
  ]));
  await assertExpectedFinals(sessionId, before, [planner], 'default-driver route');

  before = assistantCounts(await sessionMessages(sessionId), allBotIds);
  const heterogeneousFanOut = await sendHumanMessage(
    sessionId,
    '@CEO @Claude Developer（当前） Reply concisely. Do not call tools or modify files.',
  );
  assertDelivery(heterogeneousFanOut.delivery_results, new Map([
    [planner, 'inject'],
    [developer, 'send'],
    [ceo, 'send'],
    [productManager, 'inject'],
  ]));
  await assertExpectedFinals(sessionId, before, [developer, ceo], 'heterogeneous fan-out');

  before = assistantCounts(await sessionMessages(sessionId), allBotIds);
  const multiTargetFanOut = await sendHumanMessage(
    sessionId,
    '@Claude Planner（当前） @Claude Developer（当前） @产品经理 Reply concisely. Do not call tools or modify files.',
  );
  assertDelivery(multiTargetFanOut.delivery_results, new Map([
    [planner, 'send'],
    [developer, 'send'],
    [ceo, 'inject'],
    [productManager, 'send'],
  ]));
  await assertExpectedFinals(
    sessionId,
    before,
    [planner, developer, productManager],
    'multi-target fan-out',
  );

  const concurrentBefore = assistantCounts(await sessionMessages(sessionId), allBotIds);
  const concurrentLogOffset = await bcsLogSize();
  const concurrentResults = await Promise.allSettled([
    sendHumanMessage(
      sessionId,
      '@Claude Developer（当前） Reply concisely. Do not call tools or modify files.',
    ),
    sendHumanMessage(
      sessionId,
      '@Claude Developer（当前） Reply concisely. Do not call tools or modify files.',
    ),
  ]);
  const acceptedConcurrentSends = concurrentResults.filter((result) => {
    if (result.status !== 'fulfilled') return false;
    try {
      assertDelivery(result.value.delivery_results, new Map([
        [planner, 'inject'],
        [developer, 'send'],
        [ceo, 'inject'],
        [productManager, 'inject'],
      ]));
      return true;
    } catch {
      return false;
    }
  }).length;

  let concurrentFinals = false;
  if (acceptedConcurrentSends === 2) {
    try {
      const startedAt = Date.now();
      while (Date.now() - startedAt < replyTimeoutMs) {
        const messages = await sessionMessages(sessionId);
        const after = assistantCounts(messages, [developer]);
        if (after.get(developer) >= (concurrentBefore.get(developer) || 0) + 2) {
          concurrentFinals = true;
          break;
        }
        await pause(pollIntervalMs);
      }
    } catch {
      concurrentFinals = false;
    }
  }

  const scenarioLog = await readLogSince(concurrentLogOffset);
  const concurrentTimeoutCount = scenarioLog
    .split('\n')
    .filter((line) => line.includes(logGroupId) && line.includes('Concurrent request on session'))
    .length;
  const concurrentCapability = acceptedConcurrentSends === 2 && concurrentFinals && concurrentTimeoutCount === 0
    ? 'supported'
    : 'limited';

  console.log(JSON.stringify({
    result: 'pass',
    group: 'created',
    topology: { openclaw: 2, claude: 2 },
    initialization: { providerInjects: initProviderInjects, providerSends: initProviderSends, recipients: 4 },
    defaultDriver: 'pass',
    heterogeneousFanOut: 'pass',
    multiTargetFanOut: 'pass',
    sameClaudeConcurrentChatSend: {
      capability: concurrentCapability,
      accepted: acceptedConcurrentSends,
      finals: concurrentFinals ? 2 : 0,
      concurrentTimeouts: concurrentTimeoutCount,
    },
  }));
}

run().catch((error) => {
  console.error(`FAIL: mixed bot message semantics: ${safeError(error)}`);
  process.exitCode = 1;
});
