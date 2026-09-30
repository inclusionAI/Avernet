import {
  buildPrivateSessionHttpRequest,
  encodeToUrlSafeBase64,
} from '@/services/backendApi/privateChat/privateSessionController';
import { describe, expect, it } from '@jest/globals';

const connection = {
  type: 'remote',
  target: 'runtime@0:20003',
  token: 'proxy-token',
  engine_type: 'openclaw',
};

describe('privateSessionController', () => {
  it('encodes unicode session keys as URL-safe Base64', () => {
    expect(encodeToUrlSafeBase64('agent:main:客服')).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(encodeToUrlSafeBase64('agent:main:客服')).not.toContain('=');
  });

  it('routes remote requests through proxypass with its token', () => {
    expect(buildPrivateSessionHttpRequest('/api/sessions/abc/messages', connection)).toEqual({
      url: '/proxypass/runtime@0:20003/api/sessions/abc/messages',
      headers: { 'X-PROXYPASS-TOKEN': 'proxy-token' },
    });
  });

  it('routes local requests directly with bearer authentication', () => {
    expect(
      buildPrivateSessionHttpRequest('/api/sessions/abc/messages', {
        ...connection,
        type: 'local',
        target: '127.0.0.1:20003',
      }),
    ).toEqual({
      url: 'http://127.0.0.1:20003/api/sessions/abc/messages',
      headers: { Authorization: 'Bearer proxy-token' },
    });
  });
});
