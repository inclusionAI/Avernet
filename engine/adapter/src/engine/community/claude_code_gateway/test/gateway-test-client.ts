/**
 * WebSocket Gateway Test Client
 *
 * Connects to the claude-code-gateway gateway, sends a chat.send request,
 * and displays all received events including the new streams (message, content_block).
 *
 * Usage:
 *   npx tsx test/gateway-test-client.ts [message] [--host ws://127.0.0.1:18900]
 *
 * Examples:
 *   npx tsx test/gateway-test-client.ts "hello"
 *   npx tsx test/gateway-test-client.ts "list files" --host ws://localhost:18900
 */

import WebSocket from 'ws';

// ---- Config ----
const args = process.argv.slice(2);
const hostArgIdx = args.indexOf('--host');
const WS_URL = hostArgIdx >= 0 ? args.splice(hostArgIdx, 2)[1] : 'ws://127.0.0.1:18900';
const USER_MESSAGE = args[0] || 'hello, what can you do?';

const SESSION_KEY = `test-session-${Date.now()}`;

// ---- Color helpers ----
const C = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
};

function color(text: string, c: string) { return `${c}${text}${C.reset}`; }

// ---- Event display helpers ----

const STREAM_ICONS: Record<string, string> = {
  lifecycle: '⚡',
  message: '📧',
  content_block: '📦',
  thinking: '💭',
  tool: '🔧',
  command_output: '💻',
  approval: '🛡️',
  assistant: '📊',
};

function formatAgentEvent(payload: any) {
  const stream = payload.stream || '?';
  const icon = STREAM_ICONS[stream] || '❓';
  const data = payload.data || {};

  const lines: string[] = [
    color(`  ${icon} [agent] stream=${stream} seq=${payload.seq}`, C.cyan),
  ];

  switch (stream) {
    case 'lifecycle': {
      const phase = data.phase;
      if (phase === 'start') {
        lines.push(color(`    ┣ phase=start sessionId=${data.sessionId ?? '-'}`, C.green));
        if (data.tools) lines.push(color(`    ┣ tools=[${(data.tools as string[]).join(', ')}]`, C.dim));
        if (data.cwd) lines.push(color(`    ┗ cwd=${data.cwd}`, C.dim));
      } else if (phase === 'end') {
        lines.push(color(`    ┗ phase=end stopReason=${data.stopReason ?? '-'}`, C.green));
      } else {
        lines.push(color(`    ┗ phase=${phase} error=${data.error ?? '-'}`, C.red));
      }
      break;
    }
    case 'message': {
      const phase = data.phase;
      if (phase === 'start') {
        lines.push(color(`    ┣ phase=start model=${data.model ?? '-'} messageId=${data.messageId ?? '-'}`, C.magenta));
        if (data.usage) lines.push(color(`    ┗ usage: inputTokens=${data.usage.inputTokens ?? 0} outputTokens=${data.usage.outputTokens ?? 0}`, C.dim));
      } else {
        lines.push(color('    ┗ phase=stop', C.magenta));
      }
      break;
    }
    case 'content_block': {
      const phase = data.phase;
      const blockType = data.blockType;
      if (phase === 'start') {
        let detail = `phase=start blockType=${blockType} index=${data.index}`;
        if (data.toolCallId) detail += ` toolCallId=${data.toolCallId}`;
        if (data.name) detail += ` name=${data.name}`;
        lines.push(color(`    ┗ ${detail}`, C.yellow));
      } else {
        lines.push(color(`    ┗ phase=stop blockType=${blockType} index=${data.index}`, C.yellow));
      }
      break;
    }
    case 'thinking': {
      const delta = String(data.delta || '').slice(0, 80);
      lines.push(color(`    ┗ delta="${delta}${delta.length >= 80 ? '…' : ''}"`, C.dim));
      break;
    }
    case 'tool': {
      const phase = data.phase;
      if (phase === 'start') {
        lines.push(color(`    ┣ phase=start name=${data.name} toolCallId=${data.toolCallId}`, C.blue));
      } else if (phase === 'update') {
        const partial = String(data.partialArgs || '').slice(0, 60);
        lines.push(color(`    ┣ phase=update toolCallId=${data.toolCallId} partialArgs="${partial}…"`, C.dim));
      } else {
        const resultStr = JSON.stringify(data.result ?? null).slice(0, 100);
        lines.push(color(`    ┗ phase=${phase} name=${data.name} result=${resultStr}${resultStr.length >= 100 ? '…' : ''}`, C.blue));
      }
      break;
    }
    case 'command_output': {
      const detail: string[] = [ `phase=${data.phase} toolCallId=${data.toolCallId}` ];
      if (data.exitCode != null) detail.push(`exitCode=${data.exitCode}`);
      if (data.cwd) detail.push(`cwd=${data.cwd}`);
      if (data.output) detail.push(`output="${String(data.output).slice(0, 60)}"`);
      lines.push(color(`    ┗ ${detail.join(' ')}`, C.dim));
      break;
    }
    case 'approval': {
      const phase = data.phase;
      if (phase === 'requested') {
        lines.push(color(`    ┣ phase=requested kind=${data.kind} approvalId=${data.approvalId}`, C.yellow));
        lines.push(color(`    ┗ command="${String(data.command).slice(0, 80)}"`, C.dim));
      } else {
        lines.push(color(`    ┗ phase=resolved decision=${data.decision} resolvedBy=${data.resolvedBy}`, C.green));
      }
      break;
    }
    case 'assistant': {
      const parts: string[] = [];
      if (data.usage) parts.push(`usage: in=${data.usage.inputTokens ?? 0} out=${data.usage.outputTokens ?? 0} cacheR=${data.usage.cacheReadTokens ?? 0} cacheW=${data.usage.cacheCreationTokens ?? 0}`);
      if (data.costUsd != null) parts.push(`costUsd=${data.costUsd}`);
      if (data.durationMs != null) parts.push(`durationMs=${data.durationMs}`);
      if (data.numTurns != null) parts.push(`numTurns=${data.numTurns}`);
      if (data.model) parts.push(`model=${data.model}`);
      if (parts.length) lines.push(color(`    ┗ ${parts.join(' | ')}`, C.green));
      break;
    }
    default:
      lines.push(color(`    ┗ ${JSON.stringify(data).slice(0, 120)}`, C.dim));
  }
  return lines.join('\n');
}

// ---- Main ----

console.log(color('\n=== claude-code-gateway Gateway Test Client ===\n', C.bold));
console.log(`Connecting to ${WS_URL}…`);

const ws = new WebSocket(WS_URL);
let runId = '';

ws.on('open', () => {
  console.log(color('✓ Connected', C.green));
});

ws.on('message', raw => {
  const frame = JSON.parse(raw.toString());

  // connect.challenge → auto-connect
  if (frame.type === 'event' && frame.event === 'connect.challenge') {
    console.log(color('← connect.challenge', C.dim));
    const connectReq = {
      type: 'req',
      id: 'connect-1',
      method: 'connect',
    };
    ws.send(JSON.stringify(connectReq));
    console.log(color('→ connect', C.dim));
    return;
  }

  // connect response
  if (frame.type === 'res' && frame.id === 'connect-1') {
    console.log(color('✓ Handshake complete', C.green));
    console.log(color(`  Protocol: v${frame.payload?.protocol}  Server: ${frame.payload?.server?.version}`, C.dim));
    console.log(color(`  Events: ${((frame.payload?.features?.events as string[]) || []).join(', ')}`, C.dim));

    // Send chat.send
    runId = `test-run-${Date.now()}`;
    const chatReq = {
      type: 'req',
      id: 'chat-send-1',
      method: 'chat.send',
      params: {
        sessionKey: SESSION_KEY,
        message: USER_MESSAGE,
        idempotencyKey: runId,
        // v2：首次 chat.send 必须显式带 cwd，不再兜底到 relay 进程 cwd
        cwd: process.cwd(),
      },
    };
    ws.send(JSON.stringify(chatReq));
    console.log(color(`\n→ chat.send: "${USER_MESSAGE}"`, C.bold));
    console.log(color(`  sessionKey=${SESSION_KEY}  runId=${runId}\n`, C.dim));
    return;
  }

  // chat.send response
  if (frame.type === 'res' && frame.id === 'chat-send-1') {
    console.log(color(`✓ chat.send accepted: runId=${frame.payload?.runId} mode=${frame.payload?.mode}`, C.green));
    console.log(color('─'.repeat(60), C.dim));
    return;
  }

  // approval resolution request - auto-resolve with allow-once for testing
  if (frame.type === 'event' && frame.event === 'exec.approval.requested') {
    const approvalId = frame.payload?.id;
    const kind = frame.payload?.kind;
    const command = frame.payload?.request?.command;
    console.log(color(`\n🛡️  Approval requested: kind=${kind} command="${String(command).slice(0, 60)}"`, C.yellow));
    console.log(color('   Auto-resolving as allow-once…', C.dim));

    const resolveReq = {
      type: 'req',
      id: `approval-${Date.now()}`,
      method: 'exec.approval.resolve',
      params: { id: approvalId, decision: 'allow-once' },
    };
    ws.send(JSON.stringify(resolveReq));
    return;
  }

  // v3 unified interaction.requested — auto-approve exec, submit any ask_user
  if (frame.type === 'event' && frame.event === 'interaction.requested') {
    const interactionId = frame.payload?.interactionId;
    const kind = frame.payload?.kind;
    const command = frame.payload?.command;
    const decision = kind === 'exec' ? 'allow-once' : 'submit';
    console.log(color(`\n🛡️  Interaction requested: kind=${kind} id=${interactionId} command="${String(command ?? '').slice(0, 60)}"`, C.yellow));
    console.log(color(`   Auto-resolving as ${decision}…`, C.dim));
    const resolveReq = {
      type: 'req',
      id: `interaction-resolve-${Date.now()}`,
      method: 'interaction.resolve',
      params: { interactionId, decision },
    };
    ws.send(JSON.stringify(resolveReq));
    return;
  }

  // Events
  if (frame.type === 'event') {
    const evt = frame.event;

    if (evt === 'tick') return; // suppress heartbeat

    if (evt === 'chat') {
      const p = frame.payload;
      if (p.state === 'delta') {
        const text = p.message?.content?.[0]?.text || '';
        const last100 = text.slice(-100);
        process.stdout.write(color(`\r💬 [chat] delta: …${last100}`, C.green));
        return;
      }
      if (p.state === 'final') {
        const text = p.message?.content?.[0]?.text || '';
        console.log(color(`\n💬 [chat] final (stopReason=${p.stopReason}):`, C.green));
        console.log(color(`   "${text.slice(0, 200)}${text.length > 200 ? '…' : ''}"`, C.dim));
        return;
      }
      if (p.state === 'aborted' || p.state === 'error') {
        console.log(color(`\n💬 [chat] ${p.state}: ${p.errorMessage ?? ''}`, C.red));
        return;
      }
    }

    if (evt === 'agent') {
      console.log(formatAgentEvent(frame.payload));
      return;
    }

    if (evt === 'exec.approval.resolved') {
      console.log(color(`  ✓ Approval resolved: id=${frame.payload?.id} decision=${frame.payload?.decision}`, C.green));
      return;
    }

    // Generic event log
    console.log(color(`← event: ${evt}`, C.dim), JSON.stringify(frame.payload).slice(0, 120));
    return;
  }

  // Generic frame
  console.log(color(`← ${frame.type}`, C.dim), JSON.stringify(frame).slice(0, 120));
});

ws.on('close', () => {
  console.log(color('\n✗ Connection closed', C.red));
  process.exit(0);
});

ws.on('error', err => {
  console.error(color(`\n✗ Connection error: ${err.message}`, C.red));
  process.exit(1);
});

// Timeout after 120s
setTimeout(() => {
  console.log(color('\n⏱ Timeout - closing connection', C.yellow));
  ws.close();
  process.exit(0);
}, 120_000);
