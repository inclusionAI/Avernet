#!/usr/bin/env node
/**
 * Verifies that a Reviewer chat.inject reaches a cold Developer relay and is
 * visible to the Developer's first chat.send.
 *
 * The script creates one isolated local group and emits only boolean/count
 * metadata. It intentionally never prints prompts, replies, markers, IDs, or
 * credentials.
 */

import { readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const bcsBaseUrl = `http://127.0.0.1:${process.env.BCS_PORT || '21000'}`;
const mockUserId = process.env.BCS_MOCK_USER_ID || '001';
const humanActorId = `human_${mockUserId}`;
const timeoutMs = Number(process.env.CLAUDE_INJECT_CONTEXT_TIMEOUT_MS || 90_000);
const pollIntervalMs = 1_000;

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fail(message) {
  throw new Error(message);
}

function safeError(error) {
  return error instanceof Error ? error.message.replace(/\s+/g, ' ').slice(0, 240) : 'unknown error';
}

async function requestJson(pathname, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 35_000);
  try {
    const response = await fetch(`${bcsBaseUrl}${pathname}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-Mock-User-Id': mockUserId,
        ...(options.headers || {}),
      },
    });
    if (!response.ok) fail(`BCS request failed with HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('BCS request failed')) throw error;
    fail('BCS request did not complete before the local timeout');
  } finally {
    clearTimeout(timer);
  }
}

function botName(bot) {
  return bot.capabilities?.name || bot.bot_name;
}

function findOnlineBot(items, name) {
  const matches = items.filter((bot) => botName(bot) === name && bot.status === 'online' && bot.bot_uuid);
  if (matches.length !== 1) fail(`expected one online ${name} bot`);
  return matches[0];
}

async function relayBinding(role, sessionId) {
  const sessionsPath = path.join(root, `scripts/.dependencies/claude_relays/${role}/data/sessions.json`);
  const sessions = JSON.parse(await readFile(sessionsPath, 'utf8'));
  return Object.values(sessions).find((entry) => entry?.gatewaySessionKey?.includes(sessionId));
}

async function waitForRelayAssistant(role, sessionId, requestText, label) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const binding = await relayBinding(role, sessionId);
    const history = binding?.history || [];
    let requestIndex = -1;
    for (let index = history.length - 1; index >= 0; index -= 1) {
      if (history[index].role === 'user' && String(history[index].text || '').includes(requestText)) {
        requestIndex = index;
        break;
      }
    }
    if (requestIndex >= 0) {
      const answer = history.slice(requestIndex + 1).find((entry) => entry.role === 'assistant');
      if (typeof answer?.text === 'string' && answer.text.length > 0) return answer.text;
    }
    await pause(pollIntervalMs);
  }
  fail(`${label}: timed out waiting for the matching relay assistant final`);
}

function assertDelivery(response, expected) {
  const actual = new Map((response.delivery_results || []).map((result) => [result.bot_uuid, result]));
  if (actual.size !== expected.size) fail('delivery result count was unexpected');
  for (const [botId, deliveryType] of expected) {
    const result = actual.get(botId);
    if (!result || result.success !== true || result.delivery_type !== deliveryType) {
      fail('delivery result did not match the required send/inject mapping');
    }
  }
}

async function sendHumanMessage(sessionId, message) {
  return requestJson(`/sessions/${encodeURIComponent(sessionId)}/chat`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

function encodeProjectDir(cwd) {
  return cwd.replace(/\//g, '-').replace(/\./g, '-');
}

async function developerInjectRecorded(sessionId, reviewerContent) {
  const binding = await relayBinding('developer', sessionId);
  const memory = (binding.history || []).some((entry) => entry?.text?.includes(reviewerContent));
  if (!binding?.sdkSessionId || !binding.cwd) return { memory, native: false };
  const nativePath = path.join(
    process.env.CLAUDE_DEVELOPER_CONFIG_DIR || path.join(os.homedir(), '.claude-developer'),
    'projects',
    encodeProjectDir(binding.cwd),
    `${binding.sdkSessionId}.jsonl`,
  );
  let native = false;
  try {
    native = (await readFile(nativePath, 'utf8')).includes(reviewerContent);
  } catch {
    native = false;
  }
  return { memory, native };
}

async function waitForDeveloperInject(sessionId, reviewerContent, requireNative) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const result = await developerInjectRecorded(sessionId, reviewerContent);
    if (result.memory && (!requireNative || result.native)) return result;
    await pause(pollIntervalMs);
  }
  fail('Reviewer response was not recorded in both Developer session stores');
}

async function run() {
  const health = await requestJson('/health');
  if (health?.service !== 'bcs') fail('BCS health response is not a BCS service response');

  const items = (await requestJson('/bots/my')).items || [];
  const ceo = findOnlineBot(items, 'CEO');
  const developer = findOnlineBot(items, 'Claude Developer（当前）');
  const reviewer = findOnlineBot(items, 'Claude Reviewer（当前）');
  const group = await requestJson('/groups', {
    method: 'POST',
    body: JSON.stringify({
      label: `Claude inject context probe ${Date.now()}`,
      driver_bot: ceo.bot_uuid,
      originator: humanActorId,
      group_strategy: 'chat',
      participants: [
        { bot_uuid: ceo.bot_uuid, role: 'driver' },
        { bot_uuid: developer.bot_uuid, role: 'consultant' },
        { bot_uuid: reviewer.bot_uuid, role: 'consultant' },
      ],
      context: 'Automated local inject-context verification. Do not use tools or modify files.',
    }),
  });
  if (!group?.session_id) fail('new group response omitted the session ID');
  const sessionId = group.session_id;

  const reviewerRequestText = 'Reply with a unique five-word English proverb. Do not use tools, modify files, or mention any bot.';
  const reviewerRequest = await sendHumanMessage(
    sessionId,
    `@Claude Reviewer（当前） ${reviewerRequestText}`,
  );
  assertDelivery(reviewerRequest, new Map([
    [ceo.bot_uuid, 'inject'],
    [developer.bot_uuid, 'inject'],
    [reviewer.bot_uuid, 'send'],
  ]));
  const reviewerContent = (await waitForRelayAssistant(
    'reviewer',
    sessionId,
    reviewerRequestText,
    'Reviewer response',
  )).trim();
  if (!reviewerContent) fail('Reviewer final was empty');
  const beforeDeveloperSend = await relayBinding('developer', sessionId);
  if (beforeDeveloperSend?.sdkSessionId) fail('Developer unexpectedly had an SDK session before its first targeted message');
  const storedBefore = await waitForDeveloperInject(sessionId, reviewerContent, false);

  const developerRequestText = 'Reply only with the most recent complete Reviewer response from injected group context. If absent, reply NOT_FOUND. Do not use tools or modify files.';
  const developerRequest = await sendHumanMessage(
    sessionId,
    `@Claude Developer（当前） ${developerRequestText}`,
  );
  assertDelivery(developerRequest, new Map([
    [ceo.bot_uuid, 'inject'],
    [developer.bot_uuid, 'send'],
    [reviewer.bot_uuid, 'inject'],
  ]));
  const developerAnswer = await waitForRelayAssistant(
    'developer',
    sessionId,
    developerRequestText,
    'Developer context response',
  );
  const responseIncludesReviewerContent = developerAnswer.includes(reviewerContent);
  if (!responseIncludesReviewerContent) fail('Developer final did not include the injected Reviewer response');
  const storedAfter = await waitForDeveloperInject(sessionId, reviewerContent, true);

  console.log(JSON.stringify({
    result: 'pass',
    group: 'created',
    topology: { openclaw: 1, claude: 2 },
    developerInjectBeforeFirstSend: storedBefore,
    developerContextAfterFirstSend: storedAfter,
    responseIncludesReviewerContent,
  }));
}

run().catch((error) => {
  console.error(`FAIL: Claude inject context: ${safeError(error)}`);
  process.exitCode = 1;
});
