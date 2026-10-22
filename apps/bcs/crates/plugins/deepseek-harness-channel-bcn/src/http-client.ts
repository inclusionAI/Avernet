import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import type { RequestOptions } from 'node:http';
import type { ResolvedEndpoint } from './endpoint.js';
import { validateRegistrationResponse, type ProviderTokenScope } from './registration.js';
import {
  MAX_FRAME_BYTES,
  asNonEmptyString,
  asRecord,
  type RegisterBotResponse,
} from './protocol.js';

export interface HttpTransport {
  requestJson(
    endpoint: ResolvedEndpoint,
    path: string,
    options: { method: 'POST'; headers?: Record<string, string>; body?: unknown; query?: URLSearchParams },
    timeoutMs: number,
  ): Promise<unknown>;
}

export class NodeHttpTransport implements HttpTransport {
  async requestJson(
    endpoint: ResolvedEndpoint,
    path: string,
    options: { method: 'POST'; headers?: Record<string, string>; body?: unknown; query?: URLSearchParams },
    timeoutMs: number,
  ): Promise<unknown> {
    const url = new URL(path.replace(/^\/+/, ''), endpoint.baseUrl);
    if (options.query) url.search = options.query.toString();
    const body = options.body === undefined ? undefined : Buffer.from(JSON.stringify(options.body), 'utf8');
    if (body && body.byteLength > MAX_FRAME_BYTES) throw new Error('BCN HTTP request body exceeds 2 MiB');

    const requestOptions: RequestOptions = {
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || undefined,
      path: `${url.pathname}${url.search}`,
      method: options.method,
      lookup: endpoint.lookup,
      headers: {
        accept: 'application/json',
        ...(body ? { 'content-type': 'application/json', 'content-length': String(body.byteLength) } : {}),
        ...options.headers,
      },
      agent: false,
    };

    // COSEC: endpoint resolution rejects non-public destinations (except exact
    // loopback development), pins DNS, and this client never follows redirects.
    // HTTP is an explicit deployment option; production operators should prefer HTTPS.
    return new Promise((resolve, reject) => {
      const request = (url.protocol === 'https:' ? httpsRequest : httpRequest)(requestOptions, response => {
        let total = 0;
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer | string) => {
          const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          total += buffer.byteLength;
          if (total > MAX_FRAME_BYTES) {
            request.destroy(new Error('BCN HTTP response exceeds 2 MiB'));
            return;
          }
          chunks.push(buffer);
        });
        response.on('end', () => {
          const status = response.statusCode ?? 0;
          if (status < 200 || status >= 300) {
            reject(new Error(`BCN HTTP request failed with status ${status}`));
            return;
          }
          const raw = Buffer.concat(chunks).toString('utf8');
          if (!raw) {
            resolve({});
            return;
          }
          try {
            resolve(JSON.parse(raw));
          } catch {
            reject(new Error('BCN HTTP response is not valid JSON'));
          }
        });
      });
      request.setTimeout(timeoutMs, () => request.destroy(new Error('BCN HTTP request timed out')));
      request.on('error', error => reject(new Error('BCN HTTP request failed', { cause: error })));
      if (body) request.write(body);
      request.end();
    });
  }
}

export class BcnRegistrationClient {
  constructor(
    private readonly endpoint: ResolvedEndpoint,
    private readonly transport: HttpTransport,
    private readonly timeoutMs: number,
  ) {}

  async register(token: string, botName: string,
    provider?: { scope: ProviderTokenScope; providerBotRef: string },
  ): Promise<RegisterBotResponse> {
    const query = new URLSearchParams({ token, 'bot-name': botName });
    if (provider) {
      query.set('mode', 'plugin');
      query.set('provider_bot_ref', provider.providerBotRef);
    }
    const response = asRecord(await this.transport.requestJson(
      this.endpoint,
      'register',
      { method: 'POST', query },
      this.timeoutMs,
    ));
    const registeredName = asNonEmptyString(response?.bot_name);
    const botUuid = asNonEmptyString(response?.bot_uuid);
    const botToken = asNonEmptyString(response?.bot_token);
    if (!registeredName || !botUuid || !botToken) {
      throw new Error('BCN registration returned an incomplete Bot Session');
    }
    if (!provider && response?.registration != null) {
      throw new Error('BCN legacy registration unexpectedly returned Provider metadata');
    }
    const registration = provider ? validateRegistrationResponse(response?.registration, provider) : undefined;
    return { bot_name: registeredName, bot_uuid: botUuid, bot_token: botToken,
      ...(registration ? { registration } : {}) };
  }

}
