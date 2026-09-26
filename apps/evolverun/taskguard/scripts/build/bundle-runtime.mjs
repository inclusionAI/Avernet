import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { build } from "esbuild";

/** External packages that must NOT be bundled (native, plugin-loaded, or too large) */
const externalPackages = [
  "openclaw",
  "openclaw/*",
  "node:*",
  // ── zod + MCP SDK: MUST remain external ──
  // zod v4 uses a lazy __esm/$constructor pattern that breaks when bundled
  // into a single file. MCP SDK's top-level code (AssertObjectSchema = custom(...))
  // executes before ZodCustom's __esm init block runs → "Class2 is not a constructor".
  // Keeping them external means Node.js resolves them from node_modules/ at runtime,
  // which works correctly because Node loads modules eagerly in dependency order.
  // The plugin's node_modules/ (populated by pack.sh + install.sh) provides these.
  "zod",
  "zod/*",
  "@modelcontextprotocol/sdk",
  "@modelcontextprotocol/sdk/*",
  // Platform-specific SDK binary packages (~225MB each) MUST remain external.
  // We use pathToClaudeCodeExecutable to bypass require.resolve() on these,
  // pointing the SDK to the system-installed claude CLI instead.
  // The main @anthropic-ai/claude-agent-sdk package (3.5MB) is intentionally
  // NOT external — it gets inlined into the bundle so the MCP server can run
  // without node_modules/ (e.g., in marketplace plugin directory).
  "@anthropic-ai/claude-agent-sdk-darwin-arm64",
  "@anthropic-ai/claude-agent-sdk-darwin-x64",
  "@anthropic-ai/claude-agent-sdk-linux-x64",
  "@anthropic-ai/claude-agent-sdk-linux-arm64",
  "@anthropic-ai/claude-agent-sdk-linux-x64-musl",
  "@anthropic-ai/claude-agent-sdk-linux-arm64-musl",
  "@anthropic-ai/claude-agent-sdk-win32-x64",
  "@anthropic-ai/claude-agent-sdk-win32-arm64",
  ];

/** Entry points to bundle — each produces a self-contained file in dist/esm/ */
const entries = [
  { entry: "src/index.ts", outfile: "dist/esm/index.js" },
  { entry: "src/platform/mcp-entry.ts", outfile: "dist/esm/platform/mcp-entry.js" },
];

for (const { entry, outfile } of entries) {
  mkdirSync(dirname(outfile), { recursive: true });

  await build({
    entryPoints: [entry],
    outfile,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node20",
    sourcemap: false,
    legalComments: "none",
    external: externalPackages,
    // ESM bundles that include CJS packages (e.g. raw-body → depd → http-errors)
    // need a real `require` for Node built-ins like "path", "http", etc.
    banner: {
      js: `import { createRequire } from "node:module"; const require = createRequire(import.meta.url);\n`,
    },
    logLevel: "info",
  });

  const bundled = readFileSync(outfile, "utf8").replace(/[ \t]+$/gm, "");
  writeFileSync(outfile, bundled);
}
