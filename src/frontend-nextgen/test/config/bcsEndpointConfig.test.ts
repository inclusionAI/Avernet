import { SERVERS } from '../../config/servers.config';

describe('Open Core BCS endpoint config', () => {
  test('pre and prod default to the local placeholder endpoint', () => {
    const endpoint = 'http://127.0.0.1:21000';
    expect(SERVERS.LOCAL.BCS_ENDPOINT_PRE).toBe(endpoint);
    expect(SERVERS.LOCAL.BCS_ENDPOINT_PROD).toBe(endpoint);
    expect(SERVERS.PRE.BCS_ENDPOINT_PRE).toBe(endpoint);
    expect(SERVERS.PROD.BCS_ENDPOINT_PROD).toBe(endpoint);
  });
});
