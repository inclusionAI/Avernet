#!/usr/bin/env node
/**
 * Local acceptance probe for merchant_hybrid.
 *
 * Creates an isolated group with the three merchant OpenClaw bots and the
 * Claude Code platform-data bot, then verifies the group SessionContext is
 * injected into the Provider route and a targeted, read-only task produces a
 * Claude final matching the role output contract. Output contains metadata
 * only: no prompts, replies, IDs, session data, or credentials.
 */

import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bcsBaseUrl = `http://127.0.0.1:${process.env.BCS_PORT || '21000'}`;
const bcsLogPath = path.join(root, 'scripts/.dependencies/logs/bcs.log');
const mockUserId = process.env.BCS_MOCK_USER_ID || '001';
const humanActorId = `human_${mockUserId}`;
const requestTimeoutMs = Number(process.env.MERCHANT_HYBRID_REQUEST_TIMEOUT_MS || 35_000);
const replyTimeoutMs = Number(process.env.MERCHANT_HYBRID_REPLY_TIMEOUT_MS || 120_000);
const pollIntervalMs = 1_000;

const merchantBotNames = ['店长日常运营', '平台营销方案', '平台供应链'];
// Provider cards carry the `(当前)` suffix so a historical card cannot be
// mistaken for this checkout's active normalCC binding.
const claudeBotName = '平台数据分析（当前）';
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fail(message) {
  throw new Error(message);
}

function safeError(error) {
  return error instanceof Error ? error.message.replace(/\s+/g, ' ').slice(0, 240) : 'unknown error';
}

function botName(bot) {
  return bot.capabilities?.name || bot.bot_name;
}

async function requestJson(pathname, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
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
    const raw = await response.text();
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : null;
    } catch {
      // Raw BCS responses can contain chat text and must not reach test output.
    }
    if (!response.ok) fail(`BCS ${options.method || 'GET'} ${pathname} failed with HTTP ${response.status}`);
    return data;
  } catch (error) {
    if (error instanceof Error && /^BCS \S+ .* failed with HTTP \d+$/.test(error.message)) throw error;
    fail(`BCS ${options.method || 'GET'} ${pathname} did not complete before the local timeout`);
  } finally {
    clearTimeout(timer);
  }
}

function findSingleOnlineBot(items, name) {
  const matches = items.filter((bot) => botName(bot) === name && bot.status === 'online' && bot.bot_uuid);
  if (matches.length !== 1) fail(`expected exactly one online ${name} bot`);
  return matches[0];
}

async function bcsLogSize() {
  try {
    return (await stat(bcsLogPath)).size;
  } catch {
    return 0;
  }
}

async function logSince(offset) {
  try {
    const bytes = await readFile(bcsLogPath);
    return bytes.subarray(Math.min(offset, bytes.length)).toString('utf8');
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
    const window = sessionContextDispatchWindow((await logSince(offset)).split('\n'), groupId);
    if (window) return window;
    await pause(pollIntervalMs);
  }
  fail('SessionContext injection did not complete before the local timeout');
}

function countAssistantMessages(messages, botId) {
  return (messages || []).filter((message) => message.role === 'assistant' && message.sender === botId);
}

async function sessionMessages(sessionId) {
  return requestJson(`/sessions/${encodeURIComponent(sessionId)}/messages?view_bot_id=${encodeURIComponent(humanActorId)}`);
}

async function waitForClaudeFinal(sessionId, claudeId, previousCount) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < replyTimeoutMs) {
    const finals = countAssistantMessages(await sessionMessages(sessionId), claudeId);
    if (finals.length > previousCount) return finals.at(-1);
    await pause(pollIntervalMs);
  }
  fail('Claude platform-data bot did not produce a final reply before the local timeout');
}

async function deleteAcceptanceGroup(groupId, driverBotId) {
  const result = await requestJson(
    `/groups/${encodeURIComponent(groupId)}?bot_id=${encodeURIComponent(driverBotId)}`,
    { method: 'DELETE' },
  );
  if (result?.deleted !== true) fail('merchant_hybrid acceptance group was not deleted');
}

function assertDelivery(response, expected) {
  const actual = new Map((response?.delivery_results || []).map((result) => [result.bot_uuid, result]));
  const summary = [...actual.values()].reduce((counts, result) => {
    const key = `${result.delivery_type || 'unknown'}:${result.success === true ? 'success' : 'failed'}`;
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
  if (actual.size !== expected.size) {
    fail(`delivery result count was unexpected (${JSON.stringify(summary)})`);
  }
  for (const [botId, type] of expected) {
    const result = actual.get(botId);
    if (!result || result.success !== true || result.delivery_type !== type) {
      fail(`delivery result did not match the required send/inject mapping (${JSON.stringify(summary)})`);
    }
  }
}

async function run() {
  const health = await requestJson('/health');
  if (health?.service !== 'bcs') fail('BCS health response is not a BCS service response');

  const items = (await requestJson('/bots/my'))?.items || [];
  const merchantBots = merchantBotNames.map((name) => findSingleOnlineBot(items, name));
  const claudeBot = findSingleOnlineBot(items, claudeBotName);
  const allBotIds = [...merchantBots.map((bot) => bot.bot_uuid), claudeBot.bot_uuid];
  if (new Set(allBotIds).size !== 4) fail('merchant_hybrid bots do not have four distinct identities');

  const logOffset = await bcsLogSize();
  let group;
  let result;
  try {
    group = await requestJson('/groups', {
      method: 'POST',
      body: JSON.stringify({
        label: `merchant-hybrid-acceptance-${Date.now()}`,
        driver_bot: merchantBots[0].bot_uuid,
        originator: humanActorId,
        group_strategy: 'chat',
        participants: [
          { bot_uuid: merchantBots[0].bot_uuid, role: 'driver' },
          { bot_uuid: merchantBots[1].bot_uuid, role: 'consultant' },
          { bot_uuid: merchantBots[2].bot_uuid, role: 'consultant' },
          { bot_uuid: claudeBot.bot_uuid, role: 'consultant' },
        ],
        context: 'Automated local acceptance verification. Chat only; do not call tools or modify files.',
      }),
    });
    if (!group?.id || !group?.session_id) fail('new group response omitted group or session ID');
    const logGroupId = group.session_id.includes(':') ? group.session_id.split(':', 1)[0] : group.id;
    const sessionContextLines = await waitForSessionContextDispatch(logOffset, logGroupId);
    const providerInjects = sessionContextLines.filter((line) =>
      line.includes('provider downlink: deliver start') &&
      line.includes('method=chat.inject') &&
      line.includes(`target_bot_id=${claudeBot.bot_uuid}`),
    ).length;
    const contextSucceeded = sessionContextLines.some((line) =>
      line.includes('system message dispatch complete') &&
      line.includes('total_recipients=4') &&
      line.includes('successful=4') &&
      line.includes('failed=0'),
    );
    if (providerInjects !== 1 || !contextSucceeded) {
      fail('group SessionContext was not injected into the Claude Provider route');
    }

    const beforeCount = countAssistantMessages(await sessionMessages(group.session_id), claudeBot.bot_uuid).length;
    const task = await requestJson(`/sessions/${encodeURIComponent(group.session_id)}/chat`, {
      method: 'POST',
      body: JSON.stringify({
        message: '@平台数据分析（当前） 请仅基于以下 TASK_FACT 做无副作用的指标复核：版本 V-TEST-1；领取 100 张，核销 60 张；时间窗 2026-08-01 至 2026-08-07；截止时间 2026-08-07。严格输出五个短段，且每段必须分别以“结论：”“关键结果：”“校验：”“缺口：”“交接：”开头；不得合并、改写或遗漏字段。不得调用工具或修改文件。',
      }),
    });
    assertDelivery(task, new Map([
      [merchantBots[0].bot_uuid, 'inject'],
      [merchantBots[1].bot_uuid, 'inject'],
      [merchantBots[2].bot_uuid, 'inject'],
      [claudeBot.bot_uuid, 'send'],
    ]));
    const final = await waitForClaudeFinal(group.session_id, claudeBot.bot_uuid, beforeCount);
    const answer = String(final?.content || final?.text || final?.message || '');
    const requiredSections = ['结论', '关键结果', '校验', '缺口', '交接'];
    if (!requiredSections.every((section) => answer.includes(section))) {
      fail('Claude final did not satisfy the platform-data output contract');
    }
    result = {
      result: 'pass',
      group: 'created-and-cleaned',
      topology: { openclaw: 3, claude_code: 1 },
      sessionContext: { providerInjects, recipients: 4 },
      targetedClaudeChatSend: 'pass',
      outputContract: 'pass',
    };
  } finally {
    if (group?.id) await deleteAcceptanceGroup(group.id, merchantBots[0].bot_uuid);
  }

  console.log(JSON.stringify(result));
}

run().catch((error) => {
  console.error(`FAIL: merchant_hybrid live acceptance: ${safeError(error)}`);
  process.exitCode = 1;
});
