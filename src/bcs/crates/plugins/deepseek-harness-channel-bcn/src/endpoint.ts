import { lookup as dnsLookup } from 'node:dns/promises';
import { BlockList, isIP } from 'node:net';
import type { LookupFunction } from 'node:net';

const BLOCKED_IPV6 = new BlockList();
BLOCKED_IPV6.addSubnet('::', 96, 'ipv6');
BLOCKED_IPV6.addSubnet('64:ff9b::', 96, 'ipv6');
BLOCKED_IPV6.addSubnet('64:ff9b:1::', 48, 'ipv6');
BLOCKED_IPV6.addSubnet('fc00::', 7, 'ipv6');
BLOCKED_IPV6.addSubnet('fe80::', 10, 'ipv6');
BLOCKED_IPV6.addSubnet('fec0::', 10, 'ipv6');
BLOCKED_IPV6.addSubnet('ff00::', 8, 'ipv6');
BLOCKED_IPV6.addSubnet('100::', 64, 'ipv6');
BLOCKED_IPV6.addSubnet('2001::', 32, 'ipv6');
BLOCKED_IPV6.addSubnet('2001:2::', 48, 'ipv6');
BLOCKED_IPV6.addSubnet('2001:db8::', 32, 'ipv6');
BLOCKED_IPV6.addSubnet('2001:20::', 28, 'ipv6');
BLOCKED_IPV6.addSubnet('2002::', 16, 'ipv6');

export interface ResolvedEndpoint {
  readonly baseUrl: URL;
  readonly webSocketUrl: URL;
  readonly addresses: readonly { address: string; family: 4 | 6 }[];
  readonly lookup: LookupFunction;
  readonly loopback: boolean;
}

export type DnsResolver = (
  hostname: string,
) => Promise<readonly { address: string; family: number }[]>;

export async function resolveEndpoint(
  input: string,
  resolver: DnsResolver = async hostname => dnsLookup(hostname, { all: true, verbatim: true }),
): Promise<ResolvedEndpoint> {
  const baseUrl = parseEndpoint(input);
  const hostname = stripIpv6Brackets(baseUrl.hostname).toLowerCase();
  const literalFamily = isIP(hostname);
  const addresses = literalFamily
    ? [{ address: hostname, family: literalFamily }]
    : await resolver(hostname);

  if (addresses.length === 0) throw new Error('BCN endpoint hostname resolved to no addresses');
  const normalized = addresses.map(({ address, family }) => {
    const actualFamily = isIP(address);
    if ((family !== 4 && family !== 6) || actualFamily !== family) {
      throw new Error('BCN endpoint hostname returned an invalid DNS address');
    }
    return { address, family } as const;
  });

  const loopbackHost = hostname === 'localhost'
    || (literalFamily !== 0 && normalized.every(item => isLoopbackAddress(item.address)));
  if (loopbackHost) {
    if (!normalized.every(item => isLoopbackAddress(item.address))) {
      throw new Error('Loopback BCN endpoint resolved outside the loopback range');
    }
  } else if (!normalized.every(item => isPublicAddress(item.address))) {
    throw new Error('BCN endpoint resolved to a private, link-local, or reserved address');
  }

  const lookup = createPinnedLookup(hostname, normalized);
  const webSocketUrl = new URL('ws/bot', baseUrl);
  webSocketUrl.protocol = baseUrl.protocol === 'https:' ? 'wss:' : 'ws:';
  return { baseUrl, webSocketUrl, addresses: normalized, lookup, loopback: loopbackHost };
}

export function canonicalizeEndpoint(input: string): string {
  return parseEndpoint(input).toString();
}

export function parseEndpoint(input: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(input);
  } catch {
    throw new Error('BCN endpoint must be an absolute HTTP(S) URL');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('BCN endpoint must use HTTP or HTTPS');
  }
  if (!parsed.hostname) throw new Error('BCN endpoint must include a hostname');
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('BCN endpoint must not contain credentials, query parameters, or a fragment');
  }
  parsed.pathname = `${parsed.pathname.replace(/\/+$/, '')}/`;
  return parsed;
}

export function isPublicAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) return isPublicIpv4(address);
  if (family === 6) return isPublicIpv6(address);
  return false;
}

export function isLoopbackAddress(address: string): boolean {
  const normalized = address.toLowerCase();
  if (normalized === '::1') return true;
  if (normalized.startsWith('::ffff:')) return isLoopbackAddress(normalized.slice(7));
  const parts = parseIpv4(normalized);
  return parts?.[0] === 127;
}

function isPublicIpv4(address: string): boolean {
  const parts = parseIpv4(address);
  if (!parts) return false;
  const [a, b, c] = parts;
  if (a === 0 || a === 10 || a === 127 || a >= 224) return false;
  if (a === 100 && b >= 64 && b <= 127) return false;
  if (a === 169 && b === 254) return false;
  if (a === 172 && b >= 16 && b <= 31) return false;
  if (a === 192 && b === 168) return false;
  if (a === 198 && (b === 18 || b === 19)) return false;
  if (a === 192 && b === 0 && (c === 0 || c === 2)) return false;
  if (a === 198 && b === 51 && c === 100) return false;
  if (a === 203 && b === 0 && c === 113) return false;
  return true;
}

function isPublicIpv6(address: string): boolean {
  const normalized = address.toLowerCase().split('%', 1)[0] ?? '';
  if (!normalized || normalized.startsWith('::ffff:')) return false;
  return !BLOCKED_IPV6.check(normalized, 'ipv6');
}

function parseIpv4(address: string): [number, number, number, number] | undefined {
  const parts = address.split('.');
  if (parts.length !== 4) return undefined;
  const values = parts.map(part => Number(part));
  if (values.some(value => !Number.isInteger(value) || value < 0 || value > 255)) return undefined;
  return values as [number, number, number, number];
}

function stripIpv6Brackets(hostname: string): string {
  return hostname.startsWith('[') && hostname.endsWith(']') ? hostname.slice(1, -1) : hostname;
}

function createPinnedLookup(
  expectedHostname: string,
  addresses: readonly { address: string; family: 4 | 6 }[],
): LookupFunction {
  let cursor = 0;
  return ((hostname: string, options: unknown, callback: (...args: unknown[]) => void) => {
    if (stripIpv6Brackets(hostname).toLowerCase() !== expectedHostname) {
      callback(new Error('Refusing DNS lookup for an unexpected BCN hostname'));
      return;
    }
    const optionRecord = typeof options === 'object' && options !== null
      ? options as { family?: number; all?: boolean }
      : { family: typeof options === 'number' ? options : 0 };
    const matching = optionRecord.family === 4 || optionRecord.family === 6
      ? addresses.filter(item => item.family === optionRecord.family)
      : [...addresses];
    if (matching.length === 0) {
      callback(new Error('No validated BCN address matches the requested address family'));
      return;
    }
    if (optionRecord.all) {
      callback(null, matching);
      return;
    }
    const selected = matching[cursor % matching.length];
    cursor += 1;
    callback(null, selected?.address, selected?.family);
  }) as LookupFunction;
}
