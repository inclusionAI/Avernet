/**
 * Auto-generate a self-signed TLS certificate for development use.
 *
 * Uses the `openssl` CLI (available on macOS and Linux by default) to
 * generate a fresh RSA-2048 key + X.509 cert pair on every server start.
 *
 * The certificate:
 * - Is self-signed (no CA chain needed)
 * - Includes SAN: DNS:localhost, IP:127.0.0.1
 * - Is valid for 365 days from generation
 * - Uses RSA-2048 keys
 *
 * **Not for production** — MCP clients will need to trust the cert
 * explicitly (or disable cert verification). For production, use a
 * proper CA-signed cert or a reverse proxy that terminates TLS.
 *
 * @module platform/self-signed-cert
 */

import { execSync } from "child_process";

export interface TlsKeyCertPair {
  cert: string;  // PEM-encoded certificate
  key: string;   // PEM-encoded private key
}

/**
 * Generate a self-signed TLS key/cert pair for local development.
 *
 * @returns PEM-encoded cert and key
 * @throws If openssl is not available or fails
 */
export function generateSelfSignedCert(): TlsKeyCertPair {
  console.error("[clawmind:mcp] TLS: generating self-signed cert via openssl...");

  const cmd = [
    "openssl req -x509 -newkey rsa:2048 -nodes",
    "-keyout /dev/stdout -out /dev/stdout",
    "-days 365",
    '-subj "/CN=localhost/O=ClawMind-Dev/OU=MCP"',
    '-addext "subjectAltName=DNS:localhost,IP:127.0.0.1"',
  ].join(" ");

  let output: string;
  try {
    output = execSync(cmd, { encoding: "utf8", timeout: 10000 });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[clawmind:mcp] TLS: openssl failed: ${msg}`);
    throw err;
  }

  const certMatch = output.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/);
  const keyMatch = output.match(/-----BEGIN PRIVATE KEY-----[\s\S]*?-----END PRIVATE KEY-----/);

  if (!certMatch || !keyMatch) {
    console.error(`[clawmind:mcp] TLS: failed to parse cert/key from openssl output (len=${output.length})`);
    console.error(`[clawmind:mcp] TLS: openssl output preview: ${output.substring(0, 200)}`);
    throw new Error(
      `[clawmind:mcp] Failed to generate self-signed cert via openssl. ` +
      `Output: ${output.substring(0, 200)}`,
    );
  }

  const certLines = certMatch[0].split("\n").length;
  const keyLines = keyMatch[0].split("\n").length;
  console.error(`[clawmind:mcp] TLS: cert generated OK (cert=${certLines} lines, key=${keyLines} lines, CN=localhost, SAN=DNS:localhost,IP:127.0.0.1)`);

  return { cert: certMatch[0], key: keyMatch[0] };
}