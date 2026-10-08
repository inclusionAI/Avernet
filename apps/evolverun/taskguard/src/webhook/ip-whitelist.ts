/**
 * IP whitelist checking for webhook triggers.
 *
 * Supports CIDR notation (IPv4 and IPv6) and exact IP matching.
 * Empty/null whitelist allows all IPs.
 */
import { Buffer } from "node:buffer";

/**
 * Parse an IPv4 address to a 32-bit unsigned integer.
 */
function ipv4ToNumber(ip: string): number {
  const parts = ip.split(".").map(Number);
  return ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0;
}

/**
 * Parse a CIDR notation string into base IP number and mask bits.
 */
function parseCidrV4(cidr: string): { base: number; mask: number } | null {
  const [ipPart, bitsStr] = cidr.split("/");
  if (!ipPart || !bitsStr) return null;

  const bits = parseInt(bitsStr, 10);
  if (isNaN(bits) || bits < 0 || bits > 32) return null;

  const parts = ipPart.split(".");
  if (parts.length !== 4) return null;

  const base = ipv4ToNumber(ipPart);
  const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0;

  return { base: (base & mask) >>> 0, mask };
}

/**
 * Check if an IPv4 address is in a CIDR range.
 */
function isIpv4InCidr(ip: string, cidr: string): boolean {
  const parsed = parseCidrV4(cidr);
  if (!parsed) return false;

  const ipNum = ipv4ToNumber(ip);
  return (ipNum & parsed.mask) >>> 0 === parsed.base;
}

/**
 * Check if an IPv6 or IPv4 address is in a CIDR range.
 * For IPv6, uses Buffer comparison for 128-bit addresses.
 */
function isIpv6InCidr(ip: string, cidr: string): boolean {
  const [ipPart, bitsStr] = cidr.split("/");
  if (!ipPart || !bitsStr) return false;

  const bits = parseInt(bitsStr, 10);
  if (isNaN(bits) || bits < 0 || bits > 128) return false;

  // Normalize and convert to buffers
  const ipBuf = normalizeIpv6ToBuffer(ip);
  const netBuf = normalizeIpv6ToBuffer(ipPart);

  if (!ipBuf || !netBuf) return false;

  // Compare the first `bits` bits
  const fullBytes = Math.floor(bits / 8);
  const remainingBits = bits % 8;

  for (let i = 0; i < fullBytes; i++) {
    if (ipBuf[i] !== netBuf[i]) return false;
  }

  if (remainingBits > 0 && fullBytes < 16) {
    const mask = (0xff << (8 - remainingBits)) & 0xff;
    if ((ipBuf[fullBytes] & mask) !== (netBuf[fullBytes] & mask)) return false;
  }

  return true;
}

/**
 * Normalize an IPv6 address string to a 16-byte Buffer.
 * Returns null if the address is invalid.
 */
function normalizeIpv6ToBuffer(ip: string): Buffer | null {
  try {
    // Handle IPv4-mapped IPv6 addresses like ::ffff:192.168.1.1
    let expanded = ip;

    if (ip.includes(":") && ip.includes(".")) {
      // IPv4-mapped: convert the IPv4 part
      const lastColon = ip.lastIndexOf(":");
      const ipv4Part = ip.substring(lastColon + 1);
      const v4Parts = ipv4Part.split(".").map(Number);
      if (v4Parts.length === 4) {
        const hex1 = ((v4Parts[0] << 8) | v4Parts[1]).toString(16).padStart(4, "0");
        const hex2 = ((v4Parts[2] << 8) | v4Parts[3]).toString(16).padStart(4, "0");
        expanded = ip.substring(0, lastColon + 1) + hex1 + ":" + hex2;
      }
    }

    // Expand :: shorthand
    if (expanded.includes("::")) {
      const halves = expanded.split("::");
      const left = halves[0] ? halves[0].split(":") : [];
      const right = halves[1] ? halves[1].split(":") : [];
      const missing = 8 - left.length - right.length;
      const full = [...left, ...Array(missing).fill("0"), ...right];
      expanded = full.join(":");
    }

    const parts = expanded.split(":");
    if (parts.length !== 8) return null;

    const buf = Buffer.alloc(16);
    for (let i = 0; i < 8; i++) {
      const val = parseInt(parts[i], 16);
      if (isNaN(val)) return null;
      buf.writeUInt16BE(val, i * 2);
    }

    return buf;
  } catch {
    return null;
  }
}

/**
 * Check if a client IP is allowed by the whitelist.
 *
 * @param clientIp - The client's IP address
 * @param allowedIps - Array of CIDR ranges or exact IPs. Empty/null means allow all.
 * @returns true if the IP is allowed
 */
export function isIpAllowed(clientIp: string, allowedIps: string[] | null | undefined): boolean {
  // No whitelist configured — allow all
  if (!allowedIps || allowedIps.length === 0) return true;

  // Extract from X-Forwarded-For if present (already done by caller)
  const ip = clientIp.trim();

  for (const entry of allowedIps) {
    const trimmed = entry.trim();

    // Exact IP match
    if (trimmed === ip) return true;

    // CIDR notation
    if (trimmed.includes("/")) {
      const isV6 = ip.includes(":") || trimmed.includes(":");
      if (isV6) {
        if (isIpv6InCidr(ip, trimmed)) return true;
      } else {
        if (isIpv4InCidr(ip, trimmed)) return true;
      }
    }
  }

  return false;
}

/**
 * Extract the client IP from a request, handling X-Forwarded-For.
 */
export function extractClientIp(headers: Record<string, string>, socketIp?: string): string {
  const xff = headers["x-forwarded-for"];
  if (xff) {
    // X-Forwarded-For: client, proxy1, proxy2 — take the first entry
    const first = xff.split(",")[0]?.trim();
    if (first) return first;
  }
  return socketIp ?? "unknown";
}