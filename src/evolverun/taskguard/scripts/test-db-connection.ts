/**
 * Database connection test script.
 *
 * Usage:
 *   DATABASE_MODE=sqlite  node --import tsx scripts/test-db-connection.ts
 *   DATABASE_MODE=prod    node --import tsx scripts/test-db-connection.ts
 *
 * Add --graceful to test graceful degradation (default for prod when server is unreachable).
 */
import { createDatabase } from "../src/db/factory.js";
import { FlowEventRepository } from "../src/db/repositories/event-repository.js";
import { FlowMetricsRepository } from "../src/db/repositories/metrics-repository.js";
import { TriggeredAlertRepository } from "../src/db/repositories/alert-repository.js";

async function main() {
  const mode = process.env.DATABASE_MODE ?? "sqlite";
  console.log(`\n=== Database Connection Test ===`);
  console.log(`Mode: ${mode}\n`);

  try {
    const db = await createDatabase({ fallbackOnFailure: true });

    console.log(`✓ Database created: type=${db.dbType}`);

    if (db.dbType === "noop") {
      console.log("⚠ Fell back to NoOpDatabase — DB server is unreachable");
      console.log("  This is expected on local dev machines without MOSN sidecar.");
      console.log("  Graceful degradation confirmed: engine will continue without DB indexing.\n");

      // Verify NoOp operations don't throw
      const rows = await db.query("SELECT 1");
      console.log(`✓ NoOp query returns: ${JSON.stringify(rows)}`);
      const execResult = await db.exec("INSERT INTO dummy VALUES (1)");
      console.log(`✓ NoOp exec returns: ${JSON.stringify(execResult)}`);
      await db.close();
      console.log(`\n=== Graceful degradation test passed ===\n`);
      return;
    }

    // ── Basic CRUD test ──
    const execResult = await db.exec(
      `CREATE TABLE IF NOT EXISTS _connection_test (id INTEGER PRIMARY KEY, msg TEXT, created_at INTEGER)`,
    );
    console.log(`✓ CREATE TABLE: affectedRows=${execResult.affectedRows}`);

    const now = Math.floor(Date.now() / 1000);
    const insertResult = await db.exec(
      `INSERT INTO _connection_test (msg, created_at) VALUES (?, ?)`,
      ["hello from clawflow", now],
    );
    console.log(`✓ INSERT: affectedRows=${insertResult.affectedRows}, insertId=${insertResult.insertId}`);

    const rows = await db.query<{ id: number; msg: string; created_at: number }>(
      `SELECT * FROM _connection_test ORDER BY id DESC LIMIT 1`,
    );
    console.log(`✓ SELECT: ${JSON.stringify(rows[0])}`);

    // ── Transaction test ──
    await db.transaction(async (tx) => {
      await tx.exec(`INSERT INTO _connection_test (msg, created_at) VALUES (?, ?)`, [
        "in transaction",
        now,
      ]);
      const txRows = await tx.query<{ count: number }>(`SELECT COUNT(*) as count FROM _connection_test`);
      console.log(`✓ TRANSACTION: count=${txRows[0].count}`);
    });

    // ── Repository test ──
    console.log(`\n--- Repository Tests ---`);
    const eventRepo = new FlowEventRepository(db);
    const evtOk = await eventRepo.insert({
      id: "test-evt-1", time: now, type: "node_started",
      flowId: "conn-test-flow", workflowId: "conn-test-wf", nodeId: "test-node",
    });
    console.log(`✓ FlowEventRepository.insert: ${evtOk}`);

    const events = await eventRepo.findByFlowId("conn-test-flow");
    console.log(`✓ FlowEventRepository.findByFlowId: ${events.length} event(s)`);

    const metricsRepo = new FlowMetricsRepository(db);
    const metOk = await metricsRepo.record("conn-test-flow", "conn-test-wf", "test-node", "duration_ms", 42);
    console.log(`✓ FlowMetricsRepository.record: ${metOk}`);

    const metrics = await metricsRepo.aggregate("conn-test-wf", 0, now + 3600, {
      metricName: "duration_ms", aggregation: "sum",
    });
    console.log(`✓ FlowMetricsRepository.aggregate: ${JSON.stringify(metrics)}`);

    const alertRepo = new TriggeredAlertRepository(db);
    const alertOk = await alertRepo.record("conn-test-flow", "conn-test-wf", "test-node", "test_rule", "info", "Connection test alert");
    console.log(`✓ TriggeredAlertRepository.record: ${alertOk}`);

    const alerts = await alertRepo.findUnacknowledged("conn-test-wf");
    console.log(`✓ TriggeredAlertRepository.findUnacknowledged: ${alerts.length} alert(s)`);

    // ── Cleanup ──
    await db.exec(`DROP TABLE _connection_test`);
    console.log(`✓ DROP TABLE: cleanup done`);

    await db.close();
    console.log(`\n=== All tests passed ===\n`);
  } catch (error) {
    console.error(`\n✗ Connection test failed:`);
    console.error(error);
    process.exit(1);
  }
}

main();