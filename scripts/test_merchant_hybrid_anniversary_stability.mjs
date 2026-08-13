#!/usr/bin/env node
/**
 * Serial end-to-end stability acceptance for the merchant anniversary SOP.
 *
 * The driver intentionally creates fresh groups and runs for every round and
 * preserves them for later UI review. Console and trace output contain only
 * identifiers, statuses and structural assertions; the official final plan is
 * written to a dedicated Markdown file.
 */

import { mkdir, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bcsBaseUrl = `http://127.0.0.1:${process.env.BCS_PORT || '21000'}`;
const mockUserId = process.env.BCS_MOCK_USER_ID || '001';
const humanActorId = `human_${mockUserId}`;
const runCount = Number(process.env.MERCHANT_STABILITY_RUNS || 5);
const requestTimeoutMs = Number(process.env.MERCHANT_STABILITY_REQUEST_TIMEOUT_MS || 40_000);
const humanResponseTimeoutMs = Number(
  process.env.MERCHANT_STABILITY_HUMAN_RESPONSE_TIMEOUT_MS || 11 * 60_000,
);
const groupTimeoutMs = Number(process.env.MERCHANT_STABILITY_GROUP_TIMEOUT_MS || 20 * 60_000);
const runStartTimeoutMs = Number(process.env.MERCHANT_STABILITY_RUN_START_TIMEOUT_MS || 30 * 60_000);
const runFinishTimeoutMs = Number(process.env.MERCHANT_STABILITY_RUN_FINISH_TIMEOUT_MS || 60 * 60_000);
const initialContextTimeoutMs = Number(process.env.MERCHANT_STABILITY_INITIAL_CONTEXT_TIMEOUT_MS || 5 * 60_000);
const pollIntervalMs = Number(process.env.MERCHANT_STABILITY_POLL_INTERVAL_MS || 2_000);

const managerName = '店长日常运营';
const workerNames = ['平台营销方案', '平台数据分析（当前）', '平台供应链'];
const allBotNames = [managerName, ...workerNames];
const ownerAuthorization = '店主已准备好。活动固定从 2026-08-17 开始、执行周期持续 30 天；活动执行周期与券/套餐有效期是独立字段，14 天只表示剪发券有效期，60 天只表示护理套餐有效期，均不得替换 30 天活动周期。第一目标增加到店客流，第二目标提高转化率；老客主推护理套餐，新客用王牌剪发引流，私域和平台流量都可使用。沿用 KNOWLEDGE 的贡献毛利口径，活动贡献毛利率不得低于 10%；商家活动优惠承担上限 3000 元，增量采购/预付现金占用上限 4000 元。本次五轮验收的公开候选固定为：王牌剪发日常门市价 80 元/次、优惠 40 元、用户实付 40 元、最大核销 120 次、券有效期 14 天；护理套餐日常门市价 360 元/套、优惠 30 元、用户实付 330 元、最大核销 40 套、套餐有效期 60 天。这些最大核销值是当前版本必须按最坏情形保障的契约上限，不是待决项，也不是销量预测；不得用无来源转化率或保守估计自行下调。按四周分别释放剪发 30 次、护理 10 套；护理完整耗材义务 120 份，在途按时则新增采购 70 份、在途延迟则新增采购 90 份，均比较 Plan A 38 元/份三工作日与 Plan B 41 元/份一工作日。新增采购只走 KNOWLEDGE 已列的授权主供应渠道、同一 CARE-RP-01，要求包装完好、剩余有效期满足当前门店标准且批次可追溯；当前 90 份现货沿用 KNOWLEDGE 已确认的授权渠道、SKU/规格、包装、剩余有效期和批次验收，只有在途和新增采购需要到货后按同一标准复验，该复验属于 pending_external_actions，不是当前方案证据缺失。授权你在这些固定候选和 KNOWLEDGE 事实内与三个 Worker 完成补贴、产能、库存、采购和验收复核，只要不突破上述上限、品质不变且校验通过，无需再次向我确认；不得把私有履约成本 32/180 元当作门市价，也不授权实际执行外部投放或采购。one-shot input 只写稳定事实、真实 Worker 结论和 initial issues，不得固化会在 run 内变化的 checks_status；每轮 Judge 只根据当前 Manager 与上游 Worker artifact 判断当前版本。run 内 HumanInput 只验收已经复核完成的当前版本，不再把这些固定字段作为待决问题。请现在发现全部必需 Worker 并创建 manager-worker 群；平台数据角色必须选择显示名精确为“平台数据分析（当前）”的 Bot，不得选择无“（当前）”后缀的历史卡片。后续按流程推进。';
const fixedTask = `${ownerAuthorization}

今年要做18周年店庆。下周开始，活动为期一个月。

原则只有一条：品质不变。第一目标是多来客人，第二目标是提高转化率。
老客主推护理套餐，新客用王牌剪发引流。活动贡献毛利率不能低于10%。

请你协调平台营销、平台数据和平台供应链，协商出一套可执行、可验收的周年庆方案和SOP。`;
const humanAcceptance = '接受当前版本';
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function startIdleSleepGuard() {
  if (process.platform !== 'darwin') return null;
  try {
    const guard = spawn('caffeinate', ['-i', '-w', String(process.pid)], {
      stdio: 'ignore',
      detached: false,
    });
    guard.unref();
    return guard;
  } catch {
    return null;
  }
}

function timestamp() {
  return new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

const outputDir = path.resolve(
  process.env.MERCHANT_STABILITY_OUTPUT_DIR ||
    path.join(root, 'output/merchant-hybrid', `stability-5x-${timestamp()}`),
);

function fail(message) {
  throw new Error(message);
}

function safeError(error) {
  return error instanceof Error ? error.message.replace(/\s+/g, ' ').slice(0, 500) : 'unknown error';
}

function logPhase(round, phase, extra = {}) {
  console.log(JSON.stringify({ round, phase, at: new Date().toISOString(), ...extra }));
}

function botName(bot) {
  return bot.capabilities?.name || bot.bot_name;
}

async function requestJson(pathname, options = {}, timeoutMs = requestTimeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
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
      // Chat text and HTML error pages must not be copied to diagnostics.
    }
    if (!response.ok) {
      fail(`BCS ${options.method || 'GET'} ${pathname} failed with HTTP ${response.status}`);
    }
    return data;
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('BCS ')) throw error;
    fail(`BCS ${options.method || 'GET'} ${pathname} did not complete before ${timeoutMs}ms timeout`);
  } finally {
    clearTimeout(timer);
  }
}

async function waitFor(description, timeoutMs, probe) {
  const startedAt = Date.now();
  let lastProgressAt = startedAt;
  let lastDetail = 'not observed';
  while (Date.now() - startedAt < timeoutMs) {
    const result = await probe();
    if (result?.done) return result.value;
    if (result?.detail) lastDetail = result.detail;
    if (Date.now() - lastProgressAt >= 30_000) {
      console.log(JSON.stringify({
        phase: 'waiting',
        target: description,
        elapsed_seconds: Math.round((Date.now() - startedAt) / 1_000),
        detail: lastDetail,
      }));
      lastProgressAt = Date.now();
    }
    await pause(pollIntervalMs);
  }
  fail(`${description} timed out after ${Math.round(timeoutMs / 1_000)}s (${lastDetail})`);
}

function findOnlineBot(items, name) {
  const matches = items.filter((bot) => botName(bot) === name && bot.status === 'online' && bot.bot_uuid);
  if (matches.length !== 1) fail(`expected exactly one online bot named ${name}`);
  return matches[0];
}

async function discoverTopology() {
  const health = await requestJson('/health');
  if (health?.service !== 'bcs') fail('BCS health response is invalid');
  const items = (await requestJson('/bots/my'))?.items || [];
  const bots = new Map(allBotNames.map((name) => [name, findOnlineBot(items, name)]));
  const ids = [...bots.values()].map((bot) => bot.bot_uuid);
  if (new Set(ids).size !== 4) fail('merchant hybrid topology does not contain four distinct identities');
  return bots;
}

async function managerGroups(managerId) {
  const data = await requestJson(
    `/bots/${encodeURIComponent(managerId)}/groups?group_kind=all&offset=0&limit=200`,
  );
  return data?.items || [];
}

async function groupDetail(groupId) {
  return requestJson(`/groups/${encodeURIComponent(groupId)}`);
}

async function currentSession(groupId) {
  const detail = await groupDetail(groupId);
  if (detail?.latest_running_session_id) return detail.latest_running_session_id;
  const sessions = (await requestJson(`/groups/${encodeURIComponent(groupId)}/sessions`))?.items || [];
  const running = sessions.filter((session) => session.status === 'running');
  const selected = (running.length > 0 ? running : sessions)
    .sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0))[0];
  if (!selected?.id && !selected?.session_id) fail('manager-worker group has no session');
  return selected.id || selected.session_id;
}

async function sessionMessages(sessionId) {
  return requestJson(
    `/sessions/${encodeURIComponent(sessionId)}/messages?view_bot_id=${encodeURIComponent(humanActorId)}`,
  );
}

function participantIds(detail) {
  return (detail?.participants || []).map((participant) => participant.bot_uuid);
}

function hasFixedActivityPeriod(workerGroup) {
  const context = String(
    workerGroup?.context ?? workerGroup?.group_context ?? workerGroup?.metadata?.context ?? '',
  );
  return /"duration_days"\s*:\s*30\b/.test(context) || /活动.{0,20}30\s*天|30\s*天.{0,20}活动/s.test(context);
}

async function matchingNewWorkerGroup(managerId, baseline, expectedBotIds) {
  const candidates = (await managerGroups(managerId))
    .filter((group) => group.group_strategy === 'manager_worker' && !baseline.has(group.group_id))
    .sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0));
  let incompatibleContractCount = 0;
  for (const candidate of candidates) {
    const detail = await groupDetail(candidate.group_id);
    const actualBots = participantIds(detail).filter((id) => id !== humanActorId);
    if (
      actualBots.length === expectedBotIds.size &&
      actualBots.every((id) => expectedBotIds.has(id))
    ) {
      if (!hasFixedActivityPeriod(detail)) {
        incompatibleContractCount += 1;
        continue;
      }
      return { group: detail, candidateCount: candidates.length };
    }
  }
  return { group: null, candidateCount: candidates.length, incompatibleContractCount };
}

async function waitForManagerHandoff(managerId, baseline, expectedBotIds, privateSessionId) {
  let transientRetryCount = 0;
  let transientRetryAfter = 0;
  const group = await waitFor('new manager-worker group', groupTimeoutMs, async () => {
    const match = await matchingNewWorkerGroup(managerId, baseline, expectedBotIds);
    if (match.group) return { done: true, value: match.group };
    const replies = (await sessionMessages(privateSessionId))
      .filter((message) => message.role === 'assistant' && message.sender === managerId);
    const shuttingDown = replies.some((message) =>
      String(message.content || '').includes('Previous run is still shutting down'),
    );
    if (shuttingDown && transientRetryCount === 0 && transientRetryAfter === 0) {
      transientRetryAfter = Date.now() + 5_000;
    }
    if (transientRetryAfter > 0 && Date.now() >= transientRetryAfter) {
      await requestJson(`/sessions/${encodeURIComponent(privateSessionId)}/chat`, {
        method: 'POST',
        body: JSON.stringify({ message: fixedTask }),
      });
      transientRetryCount += 1;
      transientRetryAfter = 0;
    }
    return {
      done: false,
      detail: `${match.candidateCount} new manager-worker candidate(s); incompatible_contracts=${match.incompatibleContractCount || 0}; manager_replies=${replies.length}; transient_retries=${transientRetryCount}`,
    };
  });
  return { group, transientRetryCount };
}

async function waitForInitialContextReady(privateSessionId, managerId) {
  return waitFor('manager initial-context readiness', initialContextTimeoutMs, async () => {
    const replies = (await sessionMessages(privateSessionId))
      .filter((message) => message.role === 'assistant' && message.sender === managerId)
      .map((message) => String(message.content || '').trim());
    if (replies.includes('INITIAL_CONTEXT_READY')) return { done: true };
    if (replies.length > 0) {
      fail('manager acted on the empty private-group initialization context before the owner task');
    }
    return { done: false, detail: 'awaiting INITIAL_CONTEXT_READY' };
  });
}

function assertWorkerGroupContract(workerGroup) {
  if (!hasFixedActivityPeriod(workerGroup)) {
    fail('manager-worker group context omitted the fixed 30-day activity period');
  }
}

async function ensureHumanPresent(sessionId) {
  const result = await requestJson(`/sessions/${encodeURIComponent(sessionId)}/members`, {
    method: 'POST',
    body: JSON.stringify({ bot_uuid: humanActorId, role: 'observer' }),
  });
  const ids = participantIds(result);
  if (!ids.includes(humanActorId)) fail('human observer was not added to the manager-worker session');
}

function assistantSenders(messages) {
  return new Set((messages || []).filter((message) => message.role === 'assistant').map((message) => message.sender));
}

async function waitForWorkerReplies(sessionId, workerIds) {
  return waitFor('three worker replies', groupTimeoutMs, async () => {
    const messages = await sessionMessages(sessionId);
    const senders = assistantSenders(messages);
    const count = [...workerIds].filter((id) => senders.has(id)).length;
    const latestTaskStatus = messages
      .filter((message) => message.role === 'system' && String(message.content || '').startsWith('[任务状态]'))
      .sort((left, right) => Number(right.timestamp || 0) - Number(left.timestamp || 0))[0];
    const statusText = String(latestTaskStatus?.content || '');
    if (/失败: (?!-)|超时: (?!-)/.test(statusText)) {
      fail('a required worker failed or timed out before producing a final reply');
    }
    const terminalWorkerError = messages.some((message) =>
      message.role === 'assistant' &&
      workerIds.has(message.sender) &&
      /Agent response timed out before completion|LLM request failed|network connection error/i.test(
        String(message.content || ''),
      ),
    );
    if (terminalWorkerError) {
      fail('a required worker returned a terminal engine error');
    }
    const taskStatusComplete = statusText.includes('待回复: -') &&
      workerNames.every((name) => statusText.includes(name));
    const latestWorkerReplyAt = Math.max(
      0,
      ...messages
        .filter((message) => message.role === 'assistant' && workerIds.has(message.sender))
        .map((message) => Number(message.timestamp || 0)),
    );
    return count === workerIds.size && taskStatusComplete
      ? { done: true, value: { count, latestWorkerReplyAt } }
      : { done: false, detail: `${count}/${workerIds.size} worker senders; final_task_status=${taskStatusComplete}` };
  });
}

function extractRunIds(messages) {
  const ids = new Set();
  for (const message of messages || []) {
    if (typeof message.run_id === 'string' && message.run_id.startsWith('sm-')) ids.add(message.run_id);
    const matches = String(message.content || '').match(/\bsm-[0-9a-f-]{20,}\b/gi) || [];
    for (const match of matches) ids.add(match);
  }
  return [...ids];
}

async function waitForRunId(sessionId, latestWorkerReplyAt) {
  let continuationCount = 0;
  const continuationReasons = [];
  return waitFor('one-shot run ID', runStartTimeoutMs, async () => {
    const messages = await sessionMessages(sessionId);
    const ids = extractRunIds(messages);
    if (ids.length === 0) {
      const managerReplies = messages.filter((message) =>
        message.role === 'assistant' &&
        message.sender === managerName &&
        Number(message.timestamp || 0) > latestWorkerReplyAt,
      );
      if (managerReplies.some((message) =>
        String(message.content || '').includes('merchant anniversary run policy:'),
      )) {
        fail('manager collaboration run policy rejected the candidate before run start');
      }
      const managerReady = managerReplies.some((message) =>
        String(message.content || '').includes('ONE_SHOT_INPUT_READY'),
      );
      const humanPresencePrompt = managerReplies.some((message) => {
        const content = String(message.content || '');
        const explicitPresentHuman = content.includes('Present Human') &&
          (content.includes('尚未检测到') || content.includes('请确认店主'));
        const chinesePresenceGate = content.includes('HumanInput') &&
          content.includes('人类参与者') &&
          (content.includes('店主当前不在群中') || content.includes('请店主加入群'));
        return explicitPresentHuman || chinesePresenceGate;
      });
      const ownerCandidatePrompt = managerReplies.some((message) => {
        const content = String(message.content || '');
        const explicitCandidate = content.includes('DECISION_VARIABLE') &&
          (content.includes('请指示是否按以下候选推进') || content.includes('接受营销假设示例'));
        const repeatsAuthorizedPlanChoice =
          [
            '需要店主确认',
            '请店主确认',
            '向店主确认关键决策',
            '待店主确认',
          ].some((signal) => content.includes(signal)) &&
          ((content.includes('Plan A') && content.includes('Plan B')) ||
            (content.includes('方案A') && content.includes('方案B')));
        const exceedsAuthorization = /超出授权|突破.{0,8}上限|额外预算|风险接受/s.test(content);
        return explicitCandidate || (repeatsAuthorizedPlanChoice && !exceedsAuthorization);
      });
      let continuation = null;
      if (ownerCandidatePrompt && !continuationReasons.includes('owner_candidate_confirmed')) {
        continuation = {
          reason: 'owner_candidate_confirmed',
          message: '按你列出的推荐候选推进，并继续完成 permission → validate → run。该选择在首条任务已经给出的品质、毛利、优惠承担与现金占用边界内；不新增授权，也不是 run 内 HumanInput 验收。',
        };
      } else if (humanPresencePrompt && !continuationReasons.includes('present_human')) {
        continuation = {
          reason: 'present_human',
          message: '我已作为 Human observer 在当前群内。请直接执行已通过 validate 的 run；此消息只确认 Present Human，不是 run 内 HumanInput 验收，也不表示接受方案。',
        };
      } else if (managerReady && !continuationReasons.includes('one_shot_ready')) {
        continuation = {
          reason: 'one_shot_ready',
          message: '继续。你已经标记 ONE_SHOT_INPUT_READY，请不要再询问准备状态；现在在同一次激活直接执行 permission → schema load → validate → run。此消息不是 run 内 HumanInput 验收。',
        };
      }
      if (continuation) {
        await requestJson(`/sessions/${encodeURIComponent(sessionId)}/chat`, {
          method: 'POST',
          body: JSON.stringify({ message: continuation.message }),
        });
        continuationCount += 1;
        continuationReasons.push(continuation.reason);
      }
      return {
        done: false,
        detail: `no run ID yet; one_shot_continuations=${continuationCount}; reasons=${continuationReasons.join(',') || '-'}`,
      };
    }
    for (const runId of ids) {
      try {
        const view = await requestJson(`/state-machine-runs/${encodeURIComponent(runId)}`);
        if (view?.run?.session_id === sessionId) {
          return { done: true, value: { runId, continuationCount, continuationReasons } };
        }
      } catch {
        // A chat run ID can appear beside the state-machine ID; ignore it.
      }
    }
    return { done: false, detail: `${ids.length} candidate run ID(s)` };
  });
}

function validateGraph(graph, expectedBots) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const nodeIds = nodes.map((node) => String(node.node_id || ''));
  const assignees = new Set(nodes.map((node) => node.assignee_bot_id).filter(Boolean));
  const requiredWorkers = workerNames.map((name) => expectedBots.get(name).bot_uuid);
  if (!requiredWorkers.every((id) => assignees.has(id))) fail('state-machine graph omitted a required worker');
  const transitionOutcomes = new Set(edges.map((edge) => edge.outcome));
  if (!transitionOutcomes.has('approved') ||
      (!transitionOutcomes.has('revise') && !transitionOutcomes.has('blocked'))) {
    fail('state-machine graph omitted manager judge outcomes');
  }
  const managerId = expectedBots.get(managerName).bot_uuid;
  const workerRoundCounts = requiredWorkers.map((id) =>
    nodes.filter((node) => node.kind === 'bot_task' && node.assignee_bot_id === id).length,
  );
  const managerJudgeNodes = nodes.filter((node) => {
    if (node.kind !== 'bot_task' || node.assignee_bot_id !== managerId) return false;
    const outcomes = new Set(
      edges.filter((edge) => edge.source === node.node_id).map((edge) => edge.outcome),
    );
    return outcomes.has('approved') && (outcomes.has('revise') || outcomes.has('blocked'));
  });
  const reviseJudgeCount = managerJudgeNodes.filter((node) =>
    edges.some((edge) => edge.source === node.node_id && edge.outcome === 'revise'),
  ).length;
  const blockedJudgeCount = managerJudgeNodes.filter((node) =>
    edges.some((edge) => edge.source === node.node_id && edge.outcome === 'blocked'),
  ).length;
  if (workerRoundCounts.some((count) => count < 3) ||
      managerJudgeNodes.length !== 3 || reviseJudgeCount !== 2 || blockedJudgeCount !== 1) {
    fail('state-machine graph did not explicitly expand three bounded review rounds');
  }
  for (const judgeNode of managerJudgeNodes) {
    const directUpstreamAssignees = new Set(
      edges
        .filter((edge) => edge.target === judgeNode.node_id)
        .map((edge) => nodes.find((node) => node.node_id === edge.source)?.assignee_bot_id)
        .filter(Boolean),
    );
    if (!requiredWorkers.every((id) => directUpstreamAssignees.has(id))) {
      fail('manager judge is missing a direct Worker upstream');
    }
  }
  const humanNodes = nodes.filter((node) => node.kind === 'human_input');
  if (humanNodes.length !== 1 || humanNodes[0].assignee_bot_id != null) {
    fail('state-machine graph has an invalid HumanInput node');
  }
  const finalNodes = nodes.filter((node) => node.final_output === true);
  if (finalNodes.length !== 1) fail('state-machine graph must have exactly one final output');
  for (const marker of ['accepted_marker', 'changes_marker', 'blocked_marker']) {
    if (!nodeIds.includes(marker)) fail(`state-machine graph omitted ${marker}`);
  }
  const humanOutcomes = new Set(
    edges.filter((edge) => edge.source === humanNodes[0].node_id).map((edge) => edge.outcome),
  );
  if (!humanOutcomes.has('accepted') || !humanOutcomes.has('changes_requested')) {
    fail('state-machine HumanInput omitted accepted/changes_requested judge outcomes');
  }
  const incoming = new Set(edges.map((edge) => edge.target));
  const entryNodes = nodes.filter((node) => !incoming.has(node.node_id));
  if (entryNodes.length !== 1) fail('state-machine graph must have exactly one zero-indegree entry');
  const entry = entryNodes[0];
  if (!requiredWorkers.includes(entry.assignee_bot_id)) {
    fail('state-machine entry must be a first-round required Worker');
  }
  const nodeByGraphId = new Map(nodes.map((node) => [node.node_id, node]));
  const workerReachable = new Set();
  const visitedWorkerNodes = new Set();
  const queue = [entry.node_id];
  while (queue.length > 0) {
    const nodeId = queue.shift();
    if (visitedWorkerNodes.has(nodeId)) continue;
    visitedWorkerNodes.add(nodeId);
    const node = nodeByGraphId.get(nodeId);
    if (!node || !requiredWorkers.includes(node.assignee_bot_id)) continue;
    workerReachable.add(node.assignee_bot_id);
    for (const edge of edges.filter((candidate) => candidate.source === nodeId)) {
      const target = nodeByGraphId.get(edge.target);
      if (target && requiredWorkers.includes(target.assignee_bot_id)) queue.push(target.node_id);
    }
  }
  if (!requiredWorkers.every((id) => workerReachable.has(id))) {
    fail('state-machine first-round entry does not reach all required Workers before manager review');
  }
  return {
    node_count: nodes.length,
    edge_count: edges.length,
    entry_node_id: entry.node_id,
    entry_assignee_bot_id: entry.assignee_bot_id,
    human_node_id: humanNodes[0].node_id,
    final_node_id: finalNodes[0].node_id,
    node_ids: nodeIds,
  };
}

function validateExecutionTimeouts(view, expectedBots) {
  const dataBotId = expectedBots.get('平台数据分析（当前）').bot_uuid;
  for (const node of view?.nodes || []) {
    const timeout = Number(node.node_timeout_ms || 0);
    if (node.assignee_bot_id === dataBotId && timeout < 600_000) {
      fail(`Claude Code data node ${node.node_id} timeout is below 600000ms`);
    }
    if (node.assignee_bot_id && timeout < 180_000) {
      fail(`bot node ${node.node_id} timeout is below 180000ms`);
    }
    if (!node.assignee_bot_id && timeout < 600_000) {
      fail(`HumanInput node ${node.node_id} timeout is below 600000ms`);
    }
  }
}

async function waitAndRespondHuman(runId) {
  return waitFor('pending HumanInput', runFinishTimeoutMs, async () => {
    const view = await requestJson(`/state-machine-runs/${encodeURIComponent(runId)}`);
    if (['failed', 'cancelled', 'canceled'].includes(view?.run?.status)) {
      fail(`state-machine entered ${view.run.status}: ${view.run.error || 'no service error'}`);
    }
    const blockedMarker = (view?.nodes || []).find((node) => node.node_id === 'blocked_marker');
    if (blockedMarker && ['running', 'completed'].includes(blockedMarker.status)) {
      fail('state-machine entered blocked_marker before HumanInput');
    }
    if (view?.run?.status === 'completed') {
      fail('state-machine completed before reaching HumanInput');
    }
    const pending = await requestJson(`/state-machine-runs/${encodeURIComponent(runId)}/pending-human-nodes`);
    if (!Array.isArray(pending) || pending.length === 0) {
      const completed = (view?.nodes || []).filter((node) => node.status === 'completed').length;
      return { done: false, detail: `run=${view?.run?.status || 'unknown'} completed_nodes=${completed}` };
    }
    if (pending.length !== 1 || !pending[0]?.node_id) fail('unexpected pending HumanInput shape');
    await requestJson(
      `/state-machine-runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(pending[0].node_id)}/respond`,
      { method: 'POST', body: JSON.stringify({ content: humanAcceptance }) },
      humanResponseTimeoutMs,
    );
    return { done: true, value: pending[0].node_id };
  });
}

async function waitForTerminal(runId) {
  return waitFor('state-machine terminal status', runFinishTimeoutMs, async () => {
    const view = await requestJson(`/state-machine-runs/${encodeURIComponent(runId)}`);
    const status = view?.run?.status || 'unknown';
    if (['completed', 'failed', 'cancelled', 'canceled'].includes(status)) {
      return { done: true, value: view };
    }
    const completed = (view?.nodes || []).filter((node) => node.status === 'completed').length;
    return { done: false, detail: `run=${status} completed_nodes=${completed}` };
  });
}

function nodeById(view, nodeId) {
  return (view?.nodes || []).find((node) => node.node_id === nodeId);
}

function assertFinalContract(view, graphSummary, runId, humanNodeId) {
  if (view?.run?.status !== 'completed') {
    fail(`state-machine terminal status is ${view?.run?.status || 'unknown'}: ${view?.run?.error || 'no service error'}`);
  }
  const humanNode = nodeById(view, humanNodeId);
  if (humanNode?.status !== 'completed' || humanNode?.outcome !== 'accepted') {
    fail(`HumanInput did not complete with accepted outcome (${humanNode?.status || 'missing'}/${humanNode?.outcome || 'missing'})`);
  }
  const accepted = nodeById(view, 'accepted_marker');
  const changes = nodeById(view, 'changes_marker');
  const blocked = nodeById(view, 'blocked_marker');
  if (accepted?.status !== 'completed' || !String(accepted.artifact_text || '').startsWith('DELIVERY_DECISION=ACCEPTED')) {
    fail('accepted marker did not complete with the accepted decision');
  }
  if ([changes, blocked].some((node) => node?.status === 'completed')) {
    fail('a failure delivery marker completed on the accepted path');
  }
  const finalNode = nodeById(view, graphSummary.final_node_id);
  const finalText = String(finalNode?.artifact_text || '').trim();
  if (finalNode?.status !== 'completed' || !finalText) fail('official final output is missing');
  const required = [
    'DELIVERY_DECISION=ACCEPTED',
    runId,
    'Plan A',
    'Plan B',
    'MARKETING_CHECK',
    'DATA_CAPACITY_CHECK',
    'SUPPLY_FULFILLMENT_CHECK',
    'PRIVATE_FINANCIAL_CHECK',
  ];
  const missing = required.filter((value) => !finalText.includes(value));
  if (missing.length > 0) fail(`official final output omitted required markers: ${missing.join(', ')}`);
  if (!/版本|contract_version/i.test(finalText)) fail('official final output omitted the contract version');
  if (!/外部.{0,12}(动作|执行)|待.{0,12}(执行|下达|创建|配置)/s.test(finalText)) {
    fail('official final output omitted pending external actions');
  }
  return finalText;
}

function traceNodes(view) {
  return (view?.nodes || []).map((node) => ({
    node_id: node.node_id,
    assignee_bot_id: node.assignee_bot_id || null,
    status: node.status,
    outcome: node.outcome || null,
    attempt: node.attempt,
    node_timeout_ms: node.node_timeout_ms,
    error: node.error || null,
  }));
}

async function saveJson(filename, value) {
  await writeFile(path.join(outputDir, filename), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
}

async function executeRound(round, topology, globalIds) {
  const trace = {
    round,
    started_at: new Date().toISOString(),
    status: 'running',
    private_group_id: null,
    worker_group_id: null,
    session_id: null,
    run_id: null,
    phases: [],
  };
  const managerId = topology.get(managerName).bot_uuid;
  const workerIds = new Set(workerNames.map((name) => topology.get(name).bot_uuid));
  const expectedBotIds = new Set([managerId, ...workerIds]);
  const record = (phase) => {
    trace.phases.push({ phase, at: new Date().toISOString() });
    logPhase(round, phase);
  };
  try {
    record('baseline-groups');
    const baseline = new Set((await managerGroups(managerId)).map((group) => group.group_id));
    const privateGroup = await requestJson('/groups', {
      method: 'POST',
      body: JSON.stringify({
        label: `18周年店庆稳定性验收-R${round}-${Date.now()}`,
        driver_bot: managerId,
        originator: humanActorId,
        group_strategy: 'chat',
        participants: [{ bot_uuid: managerId, role: 'driver' }],
        // Keep the creation context intentionally non-business. The full owner
        // request is sent after the manager returns INITIAL_CONTEXT_READY.
        context: `五轮稳定性验收第 ${round} 轮测试私聊已建立，等待下一条店主消息。`,
      }),
    });
    if (!privateGroup?.id || !privateGroup?.session_id) fail('private group creation omitted IDs');
    trace.private_group_id = privateGroup.id;
    globalIds.privateGroups.add(privateGroup.id);
    record('private-group-created');
    await waitForInitialContextReady(privateGroup.session_id, managerId);
    record('initial-context-ready');
    await requestJson(`/sessions/${encodeURIComponent(privateGroup.session_id)}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message: fixedTask }),
    });
    record('task-sent');

    const handoff = await waitForManagerHandoff(
      managerId,
      baseline,
      expectedBotIds,
      privateGroup.session_id,
    );
    const workerGroup = handoff.group;
    trace.transient_retry_count = handoff.transientRetryCount;
    if (handoff.transientRetryCount > 0) record('transient-shutdown-retried');
    trace.worker_group_id = workerGroup.id;
    globalIds.workerGroups.add(workerGroup.id);
    record('worker-group-created');
    assertWorkerGroupContract(workerGroup);
    record('worker-group-contract-validated');
    const sessionId = await currentSession(workerGroup.id);
    trace.session_id = sessionId;
    globalIds.sessions.add(sessionId);
    await ensureHumanPresent(sessionId);
    record('human-present');
    const workerReplies = await waitForWorkerReplies(sessionId, workerIds);
    record('workers-replied');

    const runStart = await waitForRunId(sessionId, workerReplies.latestWorkerReplyAt);
    const runId = runStart.runId;
    trace.one_shot_continuation_count = runStart.continuationCount;
    trace.one_shot_continuation_reasons = runStart.continuationReasons;
    trace.run_id = runId;
    globalIds.runs.add(runId);
    record('run-started');
    const graph = await requestJson(`/state-machine-runs/${encodeURIComponent(runId)}/graph`);
    const graphSummary = validateGraph(graph, topology);
    const startedView = await requestJson(`/state-machine-runs/${encodeURIComponent(runId)}`);
    validateExecutionTimeouts(startedView, topology);
    trace.graph = graphSummary;
    record('graph-validated');
    const humanNodeId = await waitAndRespondHuman(runId);
    trace.human_node_id = humanNodeId;
    record('human-accepted');
    const terminal = await waitForTerminal(runId);
    trace.run_status = terminal?.run?.status || 'unknown';
    trace.run_error = terminal?.run?.error || null;
    trace.nodes = traceNodes(terminal);
    const finalText = assertFinalContract(terminal, graphSummary, runId, humanNodeId);
    await writeFile(path.join(outputDir, `run-${String(round).padStart(2, '0')}-plan.md`), `${finalText}\n`, {
      mode: 0o600,
    });
    trace.status = 'pass';
    trace.completed_at = new Date().toISOString();
    await saveJson(`run-${String(round).padStart(2, '0')}-trace.json`, trace);
    record('round-pass');
    return trace;
  } catch (error) {
    trace.status = 'fail';
    trace.error = safeError(error);
    trace.completed_at = new Date().toISOString();
    await saveJson(`run-${String(round).padStart(2, '0')}-trace.json`, trace);
    throw error;
  }
}

async function run() {
  const idleSleepGuard = startIdleSleepGuard();
  if (!Number.isInteger(runCount) || runCount < 1) fail('MERCHANT_STABILITY_RUNS must be a positive integer');
  await mkdir(outputDir, { recursive: true, mode: 0o700 });
  const summary = {
    started_at: new Date().toISOString(),
    requested_runs: runCount,
    passed_runs: 0,
    result: 'running',
    output_dir: outputDir,
    rounds: [],
  };
  const globalIds = {
    privateGroups: new Set(),
    workerGroups: new Set(),
    sessions: new Set(),
    runs: new Set(),
  };
  try {
    const topology = await discoverTopology();
    console.log(JSON.stringify({ phase: 'topology-ready', openclaw: 3, claude_code: 1, output_dir: outputDir }));
    for (let round = 1; round <= runCount; round += 1) {
      const result = await executeRound(round, topology, globalIds);
      summary.rounds.push({
        round,
        status: result.status,
        private_group_id: result.private_group_id,
        worker_group_id: result.worker_group_id,
        session_id: result.session_id,
        run_id: result.run_id,
      });
      summary.passed_runs += 1;
      await saveJson('summary.json', summary);
    }
    const uniqueness = [
      ['private groups', globalIds.privateGroups],
      ['worker groups', globalIds.workerGroups],
      ['sessions', globalIds.sessions],
      ['runs', globalIds.runs],
    ];
    for (const [label, ids] of uniqueness) {
      if (ids.size !== runCount) fail(`expected ${runCount} unique ${label}, got ${ids.size}`);
    }
    summary.result = 'pass';
    summary.completed_at = new Date().toISOString();
    await saveJson('summary.json', summary);
    console.log(JSON.stringify({ result: 'pass', runs: runCount, output_dir: outputDir }));
  } catch (error) {
    summary.result = 'fail';
    summary.error = safeError(error);
    summary.completed_at = new Date().toISOString();
    await saveJson('summary.json', summary);
    throw error;
  } finally {
    idleSleepGuard?.kill('SIGTERM');
  }
}

run().catch((error) => {
  console.error(`FAIL: merchant anniversary stability acceptance: ${safeError(error)}`);
  process.exitCode = 1;
});
