/**
 * Knowledge Base connectivity test script.
 *
 * Verifies that YuQue and/or AgentMind KB adapters can connect
 * and return search results for a sample query.
 *
 * Usage:
 *   node --import tsx scripts/test-kb-connection.ts
 *
 * Requires environment variables or config YAML for KB sources.
 */
import { YuQueAdapter, type YuQueAdapterConfig } from "../src/knowledge/yuque-adapter.js";
import { AgentMindAdapter, type AgentMindAdapterConfig } from "../src/knowledge/agentmind-adapter.js";

const TEST_QUERY = "工作流节点重试策略";

async function testYuQue(config: YuQueAdapterConfig): Promise<void> {
  console.log("\n── YuQue Adapter ──");
  console.log(`  Base URL: ${config.apiBaseUrl}`);
  console.log(`  Token: ${config.authToken ? "****" + config.authToken.slice(-4) : "(not set)"}`);

  const adapter = new YuQueAdapter(config);
  try {
    const results = await adapter.search(TEST_QUERY, 3);
    console.log(`  ✓ Search returned ${results.length} results`);
    for (const r of results) {
      console.log(`    - [${r.source}] ${r.title} (relevance: ${r.relevance.toFixed(2)})`);
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.log(`  ✗ Search failed: ${msg}`);
  }
}

async function testAgentMind(config: AgentMindAdapterConfig): Promise<void> {
  console.log("\n── AgentMind Adapter ──");
  console.log(`  API URL: ${config.apiBaseUrl}`);
  console.log(`  Token: ${config.token ? "****" + config.token.slice(-4) : "(not set)"}`);

  const adapter = new AgentMindAdapter(config);
  try {
    const results = await adapter.search(TEST_QUERY, 3);
    console.log(`  ✓ Search returned ${results.length} results`);
    for (const r of results) {
      console.log(`    - [${r.source}] ${r.title} (relevance: ${r.relevance.toFixed(2)})`);
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.log(`  ✗ Search failed: ${msg}`);
  }
}

async function main(): Promise<void> {
  console.log("\n=== Knowledge Base Connectivity Test ===");
  console.log(`Test query: "${TEST_QUERY}"\n`);

  const yuqueToken = process.env.YUQUE_TOKEN ?? "";
  const yuqueBaseUrl = process.env.YUQUE_BASE_URL ?? "https://yuque.example.com";
  const amToken = process.env.AGENTMIND_TOKEN ?? "";
  const amApiUrl = process.env.AGENTMIND_API_URL ?? "https://agentmind.example.com/api";

  let tested = 0;

  if (yuqueToken) {
    await testYuQue({ authToken: yuqueToken, apiBaseUrl: yuqueBaseUrl });
    tested++;
  } else {
    console.log("\n── YuQue Adapter ──");
    console.log("  ⚠ Skipped: YUQUE_TOKEN not set");
  }

  if (amToken) {
    await testAgentMind({
      token: amToken,
      apiBaseUrl: amApiUrl,
      instanceName: process.env.AGENTMIND_INSTANCE_NAME ?? "default",
      interfaceName: process.env.AGENTMIND_INTERFACE_NAME ?? "default",
      userName: process.env.AGENTMIND_USER_NAME ?? "test",
      userId: process.env.AGENTMIND_USER_ID ?? "test",
    });
    tested++;
  } else {
    console.log("\n── AgentMind Adapter ──");
    console.log("  ⚠ Skipped: AGENTMIND_TOKEN not set");
  }

  if (tested === 0) {
    console.log("\n⚠ No KB adapters configured. Set YUQUE_TOKEN and/or AGENTMIND_TOKEN to test.");
  } else {
    console.log(`\n✓ Tested ${tested} adapter(s).`);
  }
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});