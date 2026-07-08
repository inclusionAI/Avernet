// Direct-to-relay R2 driver: connect to relay :18900, send msg1, then fire
// msg2+msg3 while busy. Asserts relay returns status=queued and (via logs)
// enqueues + flushes-merged. Run with: npx tsx test/r2-direct-driver.ts
import WebSocket from 'ws';

const URL = process.env.RELAY_URL || 'ws://127.0.0.1:18900';
const SK = `agent:claude-code-ws:session:r2direct-${Date.now()}:user:claude-code-ws`;
const CWD = '/tmp';

type Frame = { type: string; id?: string; ok?: boolean; payload?: any; event?: string };

function send(ws: WebSocket, id: string, method: string, params: any) {
  ws.send(JSON.stringify({ type: 'req', id, method, params }));
}

async function main() {
  const ws = new WebSocket(URL);
  const responses: Record<string, Frame> = {};
  const events: Frame[] = [];
  let finals = 0;

  ws.on('message', raw => {
    let f: Frame;
    try { f = JSON.parse(raw.toString()); } catch { return; }
    if (f.type === 'event') {
      events.push(f);
      const st = (f.payload && (f.payload.state || (f.payload.data && f.payload.data.state))) || '';
      if (st === 'final' || st === 'completed' || st === 'aborted') finals++;
      // auto-connect on challenge
      if (f.event === 'connect.challenge') {
        ws.send(JSON.stringify({ type: 'req', id: 'connect-1', method: 'connect' }));
      }
      return;
    }
    if (f.type === 'res' && f.id) responses[f.id] = f;
  });

  await new Promise<void>((res, rej) => { ws.once('open', () => res()); ws.once('error', rej); });

  // wait for connect res
  const t0 = Date.now();
  while (!responses['connect-1'] && Date.now() - t0 < 8000) await new Promise(r => setTimeout(r, 50));
  console.log('connect ok:', responses['connect-1']?.ok);

  // msg1 (long-ish so the run stays busy)
  send(ws, 'm1', 'chat.send', { sessionKey: SK, message: 'Count slowly from 1 to 8, one per line.', idempotencyKey: 'm1k', cwd: CWD });
  // tiny gap, then 2 more while busy
  await new Promise(r => setTimeout(r, 400));
  send(ws, 'm2', 'chat.send', { sessionKey: SK, message: 'Also one color.', idempotencyKey: 'm2k', cwd: CWD });
  await new Promise(r => setTimeout(r, 200));
  send(ws, 'm3', 'chat.send', { sessionKey: SK, message: 'And one animal.', idempotencyKey: 'm3k', cwd: CWD });

  // collect responses for m2/m3 and wait for two finals (run1 + merged flush)
  const deadline = Date.now() + 200000;
  while (Date.now() < deadline) {
    if (responses['m2'] && responses['m3'] && finals >= 2) break;
    await new Promise(r => setTimeout(r, 300));
  }

  const p1 = responses['m1']?.payload;
  const p2 = responses['m2']?.payload;
  const p3 = responses['m3']?.payload;
  console.log('m1 res:', JSON.stringify(responses['m1'] ?? null));
  console.log('m2 res:', JSON.stringify(responses['m2'] ?? null));
  console.log('m3 res:', JSON.stringify(responses['m3'] ?? null));
  console.log('finals seen:', finals);

  const queuedSeen = [p2, p3].filter(p => p && p.status === 'queued');
  console.log('QUEUED responses:', JSON.stringify(queuedSeen));
  console.log('RESULT:', queuedSeen.length >= 1 ? 'QUEUED-OK' : 'NO-QUEUE');

  await new Promise<void>(res => { ws.once('close', () => res()); ws.close(); });
}

main().catch(e => { console.error('ERR', e); process.exit(1); });
