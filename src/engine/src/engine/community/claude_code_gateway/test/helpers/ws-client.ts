import WebSocket from 'ws';
import type { GatewayFrame, GatewayResponseFrame } from '../../src/types.js';

export type CollectedEvent = { event: string; payload: unknown; seq?: number };

export class TestGatewayClient {
  ws: WebSocket;
  private nextId = 1;
  private pending = new Map<string, { resolve:(f: GatewayResponseFrame) => void; reject: (e: Error) => void }>();
  events: CollectedEvent[] = [];
  private eventWaiters: Array<{ predicate: (e: CollectedEvent) => boolean; resolve: (e: CollectedEvent) => void }> = [];
  closed = false;

  constructor(url: string) {
    this.ws = new WebSocket(url);
  }

  async open(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      this.ws.once('open', () => resolve());
      this.ws.once('error', err => reject(err));
    });
    this.ws.on('message', buf => this.onMessage(buf.toString()));
    this.ws.on('close', () => { this.closed = true; });
  }

  private onMessage(raw: string) {
    let frame: GatewayFrame;
    try { frame = JSON.parse(raw) as GatewayFrame; } catch { return; }
    if (frame.type === 'res') {
      const waiter = this.pending.get(frame.id);
      if (waiter) {
        this.pending.delete(frame.id);
        waiter.resolve(frame);
      }
      return;
    }
    if (frame.type === 'event') {
      const collected: CollectedEvent = { event: frame.event, payload: frame.payload, seq: frame.seq };
      this.events.push(collected);
      this.eventWaiters = this.eventWaiters.filter(w => {
        if (w.predicate(collected)) {
          w.resolve(collected);
          return false;
        }
        return true;
      });
    }
  }

  request(method: string, params: Record<string, unknown> = {}): Promise<GatewayResponseFrame> {
    const id = `req-${this.nextId++}`;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ type: 'req', id, method, params }));
    });
  }

  /** Raw send (used to send malformed payloads in tests). */
  sendRaw(raw: string) {
    this.ws.send(raw);
  }

  waitForEvent(predicate: (e: CollectedEvent) => boolean, timeoutMs = 3000): Promise<CollectedEvent> {
    for (const ev of this.events) {
      if (predicate(ev)) return Promise.resolve(ev);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.eventWaiters = this.eventWaiters.filter(w => w.resolve !== resolve);
        reject(new Error(`waitForEvent: timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.eventWaiters.push({
        predicate,
        resolve: ev => {
          clearTimeout(timer);
          resolve(ev);
        },
      });
    });
  }

  async close(): Promise<void> {
    if (this.closed) return;
    await new Promise<void>(resolve => {
      this.ws.once('close', () => resolve());
      this.ws.close();
    });
  }
}

/** Wait a small idle interval; useful after an abort/disconnect to let async cleanup settle. */
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
